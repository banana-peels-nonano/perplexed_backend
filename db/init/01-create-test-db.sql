-- Runs once when the database volume is first created.
-- Tests get their own database so a test run can never wipe your dev data.
CREATE DATABASE perplexed_test OWNER perplexed;
