-- Migration: 2026-05-08 — knowledge_store.metadata jsonb column
-- Applied live in production: 2026-05-08
-- Locked per DOCTRINE-NEVSKY-OMNISCIENCE-01.

-- ============================================================
-- Purpose
-- ------------------------------------------------------------
-- Adds a jsonb metadata column to knowledge_store so structured
-- payloads (Yakov session handoffs, future structured ingests)
-- can travel alongside the human-readable content/title without
-- polluting the content field with JSON blobs.
--
-- First consumer: /yakov/handoff endpoint, which writes session
-- handoffs as content_type='session_note' with full structured
-- payload (changes, next_session_priorities, doctrine_notes,
-- high_impact, instance) in metadata.
--
-- The GIN index supports jsonb containment queries used by
-- Sophia/Nevsky-side filtering, e.g.:
--   SELECT * FROM knowledge_store
--   WHERE metadata @> '{"instance": "yakov-droplet"}';
--   SELECT * FROM knowledge_store
--   WHERE metadata @> '{"high_impact": true}';
-- ============================================================

ALTER TABLE knowledge_store
  ADD COLUMN metadata jsonb;

CREATE INDEX idx_knowledge_store_metadata
  ON knowledge_store USING gin (metadata);
