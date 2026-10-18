-- data/DDL.sql  (safe to re-run; requires MariaDB 11.8+)
CREATE DATABASE IF NOT EXISTS resumematch
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE resumematch;

-- 1. Scraped jobs
CREATE TABLE IF NOT EXISTS jobs (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  source      VARCHAR(50)  NOT NULL,          -- e.g. greenhouse
  external_id VARCHAR(100),                   -- the site's own job id
  title       VARCHAR(500) NOT NULL,
  company     VARCHAR(255),
  location    VARCHAR(255),
  url         VARCHAR(1000),
  url_hash    CHAR(64)     NOT NULL UNIQUE,   -- SHA-256 of the url, used to dedupe
  description MEDIUMTEXT,
  scraped_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_company (company),
  INDEX idx_title (title(100))
) ENGINE=InnoDB;

-- 2. One vector per job (only ONE vector index is allowed per table)
CREATE TABLE IF NOT EXISTS job_embeddings (
  job_id     BIGINT PRIMARY KEY,
  model      VARCHAR(100) NOT NULL,           -- e.g. BAAI/bge-m3
  embedding  VECTOR(1024) NOT NULL,           -- BGE-M3 = 1024 dimensions
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  VECTOR INDEX (embedding) M=8 DISTANCE=cosine,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 3. Your resume(s). No vector index needed, you only compare against it
CREATE TABLE IF NOT EXISTS resumes (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  name         VARCHAR(100) NOT NULL,
  content      MEDIUMTEXT   NOT NULL,
  content_hash CHAR(64)     NOT NULL UNIQUE,
  model        VARCHAR(100) NOT NULL,
  embedding    VECTOR(1024) NOT NULL,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 4. Saved top matches (this replaces appending to a CSV; export from here)
CREATE TABLE IF NOT EXISTS match_results (
  resume_id  BIGINT NOT NULL,
  job_id     BIGINT NOT NULL,
  role       VARCHAR(100),
  score      DOUBLE NOT NULL,                 -- 1 - cosine distance
  matched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (resume_id, job_id),
  FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id)    REFERENCES jobs(id)    ON DELETE CASCADE
) ENGINE=InnoDB;