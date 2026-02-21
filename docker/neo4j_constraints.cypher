// Uniqueness constraints — also create a backing index automatically
CREATE CONSTRAINT paper_id_unique IF NOT EXISTS
  FOR (p:Paper) REQUIRE p.id IS UNIQUE;

CREATE CONSTRAINT author_id_unique IF NOT EXISTS
  FOR (a:Author) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT institution_id_unique IF NOT EXISTS
  FOR (i:Institution) REQUIRE i.id IS UNIQUE;

CREATE CONSTRAINT topic_id_unique IF NOT EXISTS
  FOR (t:Topic) REQUIRE t.id IS UNIQUE;

// Additional indexes for common query patterns
CREATE INDEX paper_year_idx IF NOT EXISTS
  FOR (p:Paper) ON (p.publication_year);

CREATE INDEX paper_citation_count_idx IF NOT EXISTS
  FOR (p:Paper) ON (p.citation_count);
