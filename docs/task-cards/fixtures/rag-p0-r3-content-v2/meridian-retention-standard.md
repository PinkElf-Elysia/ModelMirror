# MER-209 Data Retention Standard

## Telemetry retention

MER-209 production telemetry is retained for forty-five days in the Quartz Vault. The storage owner is the Data Reliability group. Raw device identifiers are replaced with rotating pseudonyms before ingestion.

## Deletion review

Routine deletion runs every Tuesday at 02:00 UTC and requires compliance lead Elena Park to approve the deletion manifest. The manifest is preserved for one year in the Indigo Register.

## Legal hold

A legal hold pauses routine deletion only for the named case identifier. Legal holds require the general counsel signature and expire after ninety days unless renewed. A hold never permits exporting raw device identifiers.

## Backup boundary

Encrypted backups are retained for twenty-one days. Restore drills use the Cedar checkpoint and must finish within four hours. Backup data cannot be copied into analytics sandboxes.
