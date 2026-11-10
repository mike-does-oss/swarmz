Numbered migrations. `schema.sql` (one directory up) is migration 001.
Add `002_<name>.sql`, `003_<name>.sql`, ... here; `swarmz.registry.db.migrate`
applies any not yet recorded in `schema_version`, in order, each in a transaction.
