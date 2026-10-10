-- LA BASE D'UN PROJET, ET SON COMPTE A ELLE SEULE.
--
-- Chaque base de projet a son propre role Postgres, du meme nom, qui en est
-- le proprietaire et le seul (avec l'administrateur) a pouvoir s'y
-- connecter. Une application, une session VS Code ou un agent de code qui
-- travaille sur un projet n'a que ce compte-la : les autres bases, et
-- celles de la stack (dagster, codelab), lui sont fermees par Postgres
-- lui-meme.
--
-- Joue par psql, connecte en administrateur a la base d'instance, depuis
-- trois endroits : codelab-postgres au demarrage (toutes les bases), le
-- panneau au lancement d'une application, le poste de developpement pour
-- « codelab db ». Idempotent : le rejouer ne change rien de ce qui est en
-- place, et rattrape ce qui manque.
--
-- Parametres, par l'ENVIRONNEMENT (\getenv) et jamais par la ligne de
-- commande, ou « ps » les montrerait :
--   CODELAB_BASE       le nom de la base (et du role)
--   CODELAB_CLE_BASES  la cle dont derive le mot de passe du role
--   CODELAB_SCHEMA     le schema du projet (defaut : dagster)
--
-- LE MOT DE PASSE N'EST STOCKE NULLE PART : il se derive de la cle (le mot
-- de passe administrateur de credentials.env) et du nom de la base,
--   sha256(cle || ':codelab-base:' || base), en hexadecimal.
-- Chaque service qui detient la cle le recalcule ; une cle changee change
-- tous les mots de passe, que codelab-postgres realigne au demarrage. Meme
-- formule cote Python : mot_de_passe_base() dans app.py et le poste.

\set ON_ERROR_STOP on
\set QUIET on
SET client_min_messages = warning;
\getenv base CODELAB_BASE
\getenv cle CODELAB_CLE_BASES
\getenv schema CODELAB_SCHEMA
\if :{?schema}
\else
\set schema dagster
\endif

SELECT encode(sha256(convert_to(:'cle' || ':codelab-base:' || :'base', 'UTF8')), 'hex') AS mdp
\gset

-- 1. La base, le role, et qui peut s'y connecter. -----------------------------

SELECT format('CREATE DATABASE %I', :'base')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'base')
\gexec
SELECT format('CREATE ROLE %I', :'base')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'base')
\gexec
-- Aucun privilege de plus que se connecter : ni creer de base, ni de role,
-- ni contourner les droits.
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
              'NOREPLICATION NOBYPASSRLS PASSWORD %L', :'base', :'mdp')
\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'base', :'base')
\gexec
-- PUBLIC avait CONNECT sur toute base : c'est ce qui laissait n'importe quel
-- compte entrer partout. On le retire, et seul le role du projet le garde.
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'base')
\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'base', :'base')
\gexec
-- Et les bases de la stack -- celle d'instance, ou ce script est connecte,
-- et celle du panneau -- sont fermees a tout autre compte que
-- l'administrateur, des la premiere base de projet : pas seulement au
-- prochain redemarrage de codelab-postgres.
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', datname)
  FROM pg_database WHERE datname IN (current_database(), 'codelab')
\gexec
-- Le schema du projet en tete : « CREATE TABLE t » y atterrit, comme pour
-- l'administrateur (voir postgres/entrypoint.sh).
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path TO %I, public',
              :'base', :'base', :'schema')
\gexec
-- Et pour l'administrateur (Dagster ecrit avec lui) : meme schema en tete.
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path TO %I, public',
              current_user, :'base', :'schema')
\gexec

-- 2. Dans la base : ce qui existe deja passe au role du projet. -------------

\connect :"base"
\set ON_ERROR_STOP on
SET client_min_messages = warning;

SELECT format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I', :'schema', :'base')
\gexec

-- Les schemas crees par l'administrateur (celui du projet, d'autres).
-- « public » appartient deja au proprietaire de la base.
SELECT format('ALTER SCHEMA %I OWNER TO %I', nspname, :'base')
  FROM pg_namespace
 WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'public')
   AND nspname NOT LIKE 'pg\_%'
   AND pg_get_userbyid(nspowner) <> :'base'
\gexec

-- Tables, vues, sequences : celles ecrites jusqu'ici par l'administrateur
-- (Dagster, les applications d'avant) deviennent celles du projet. Sauf ce
-- qui appartient a une extension (pgvector), et les sequences rattachees a
-- une colonne, qui suivent leur table.
SELECT format('ALTER %s %I.%I OWNER TO %I',
              CASE c.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
                             WHEN 'S' THEN 'SEQUENCE' WHEN 'f' THEN 'FOREIGN TABLE'
                             ELSE 'TABLE' END,
              n.nspname, c.relname, :'base')
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
   AND n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND n.nspname NOT LIKE 'pg\_%'
   AND pg_get_userbyid(c.relowner) <> :'base'
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                      AND d.deptype IN ('e', 'a', 'i'))
\gexec

-- Les fonctions et procedures (pas les agregats, ni ce qui vient d'une
-- extension).
SELECT format('ALTER ROUTINE %s OWNER TO %I', p.oid::regprocedure, :'base')
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND n.nspname NOT LIKE 'pg\_%'
   AND p.prokind IN ('f', 'p')
   AND pg_get_userbyid(p.proowner) <> :'base'
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                      AND d.deptype = 'e')
\gexec

-- 3. Ce que l'administrateur creera ENSUITE (Dagster ecrit ses tables avec
-- lui) reste lisible et modifiable par le projet. ----------------------------

SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT ALL ON TABLES TO %I',
              current_user, :'base')
\gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT ALL ON SEQUENCES TO %I',
              current_user, :'base')
\gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT ALL ON FUNCTIONS TO %I',
              current_user, :'base')
\gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT ALL ON SCHEMAS TO %I',
              current_user, :'base')
\gexec
