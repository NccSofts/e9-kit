-- Init do Postgres de cada run (v0.7), rodado pela imagem na primeira subida.
-- O papel da aplicação NÃO é superuser e só tem CREATEDB, como deve ser na produção. A extensão
-- vector vai ao template1 como superuser, porque o papel não consegue criá-la; todo banco criado
-- depois herda. Sem senha aqui, de propósito: o harness gera uma por run e a põe pelo stdin do psql.
CREATE ROLE app_user WITH LOGIN CREATEDB;
\connect template1
CREATE EXTENSION IF NOT EXISTS vector;
\connect postgres
CREATE DATABASE app_dev OWNER app_user;
CREATE DATABASE app_test OWNER app_user;
