-- Stable pseudonyms for personas. Swarm agents post under these names (A2), so the same
-- persona carries the same public name on every question, and memories about "who said what"
-- transfer. Filled by swarmz.registry.names.ensure_names; deterministic from persona_id.
ALTER TABLE personas ADD COLUMN IF NOT EXISTS display_name VARCHAR;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS handle VARCHAR;
