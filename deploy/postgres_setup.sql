CREATE USER carace WITH PASSWORD 'change-me';
CREATE DATABASE carace OWNER carace;
\c carace
GRANT ALL PRIVILEGES ON DATABASE carace TO carace;
