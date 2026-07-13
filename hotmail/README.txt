Hotmail OAuth pool (optional, personal use only).

Active bulk dumps have been moved out of this tree to:
  sso_to_cliproxy_tool/account_data/backups/hotmail_archive_*/

To use occasionally:
  1) put email----password----client_id----refresh_token lines in tokens.txt
  2) set config: "email_provider": "hotmail"
                 "hotmail_tokens_file": "./hotmail/tokens.txt"

Default registration uses Cloudflare domain mail (sf4), not hotmail.
