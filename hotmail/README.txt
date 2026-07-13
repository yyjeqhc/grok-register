Hotmail OAuth pool for email_provider=hotmail

Format (one per line):
  email----password----client_id----refresh_token

Config:
  "email_provider": "hotmail",
  "hotmail_tokens_file": "./hotmail/alive_sample7.txt"

On claim, line is removed from the pool file immediately.
  *_used.txt  — registration finished (SSO saved)
  *_dead.txt  — refresh/IMAP dead
  (retry)     — put back into pool on code/reg failure

alive_sample7.txt: 7 accounts previously IMAP-probed.
