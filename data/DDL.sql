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
  embedding   VECTOR(1024),                   -- BGE-M3 = 1024 dimensions (NULL until embedded)
  scraped_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_company (company),
  INDEX idx_title (title(100))
) ENGINE=InnoDB;

-- 2. Companies / Board status (logs unmatched companies or scraper status)
CREATE TABLE IF NOT EXISTS companies (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  company     VARCHAR(255) NOT NULL,
  reason      VARCHAR(100) NOT NULL,          -- e.g. NoMatchingCompany, NoMatchingJob
  scraped_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_company (company)
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

-- 5. User and role setup
-- Grants privileges for application user 'resumematch'
CREATE USER IF NOT EXISTS 'resumematch'@'localhost' IDENTIFIED BY 'resumematch_pass';
GRANT ALL PRIVILEGES ON resumematch.* TO 'resumematch'@'localhost';

CREATE USER IF NOT EXISTS 'resumematch'@'127.0.0.1' IDENTIFIED BY 'resumematch_pass';
GRANT ALL PRIVILEGES ON resumematch.* TO 'resumematch'@'127.0.0.1';

FLUSH PRIVILEGES;
