<?php
// Reads EspoCRM config, decrypts IMAP passwords, outputs a JSON array of accounts.
// Called by watcher.py at startup and every RELOAD_INTERVAL to auto-discover mailboxes.

$internal = require '/var/www/html/data/config-internal.php';

$key = hash('sha256', $internal['cryptKey'], true);
$db  = $internal['database'];

$pdo = new PDO(
    "mysql:host={$db['host']};port={$db['port']};dbname={$db['dbname']};charset=utf8mb4",
    $db['user'],
    $db['password'],
    [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
);

function decrypt(string $enc, string $key): ?string {
    $data = base64_decode($enc, true);
    if ($data === false || strlen($data) <= 16) {
        return null;
    }
    $ct  = substr($data, 0, -16);
    $iv  = substr($data, -16);
    $val = openssl_decrypt($ct, 'aes-256-cbc', $key, OPENSSL_RAW_DATA, $iv);
    return $val !== false ? trim($val) : null;
}

$accounts = [];

$queries = [
    "SELECT username, password, host, port, security
       FROM inbound_email
      WHERE deleted=0 AND use_imap=1 AND status='Active'",
    "SELECT username, password, host, port, security
       FROM email_account
      WHERE deleted=0 AND use_imap=1 AND status='Active'",
];

foreach ($queries as $sql) {
    foreach ($pdo->query($sql) as $row) {
        $pw = decrypt($row['password'] ?? '', $key);
        if ($pw === null || $pw === '') {
            continue;
        }
        $accounts[] = [
            'email'    => $row['username'],
            'password' => $pw,
            'host'     => $row['host'],
            'port'     => (int) ($row['port'] ?: 993),
            'security' => $row['security'] ?: 'SSL',
        ];
    }
}

echo json_encode($accounts);
