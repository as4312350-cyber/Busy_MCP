/*
  BUSY SQL edition: create a login that can only READ BUSY databases.
  Run in SQL Server Management Studio (or sqlcmd -S .\SQLEXPRESS -E -i this_file.sql)
  as an administrator. Change the password first.

  Re-run after BUSY creates a new financial-year database so the new one is covered too.
*/
USE master;
GO
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'busy_reader')
    CREATE LOGIN busy_reader WITH PASSWORD = N'Change-This-Strong-Password-1!', CHECK_POLICY = ON;
GO

DECLARE @db sysname, @sql nvarchar(max);
DECLARE dbs CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.databases WHERE name LIKE N'Busy%' AND state = 0;
OPEN dbs;
FETCH NEXT FROM dbs INTO @db;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'USE ' + QUOTENAME(@db) + N';
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N''busy_reader'')
    CREATE USER busy_reader FOR LOGIN busy_reader;
ALTER ROLE db_datareader ADD MEMBER busy_reader;
DENY INSERT, UPDATE, DELETE, EXECUTE, ALTER, REFERENCES TO busy_reader;
DENY CREATE TABLE, CREATE VIEW, CREATE PROCEDURE, CREATE FUNCTION TO busy_reader;';
    EXEC sys.sp_executesql @sql;
    PRINT N'busy_reader granted read-only access to ' + @db;
    FETCH NEXT FROM dbs INTO @db;
END
CLOSE dbs;
DEALLOCATE dbs;
GO
/* Then configure busy-mcp with:
     BUSY_SQL_SERVER=<your server\instance>
     BUSY_SQL_USER=busy_reader
     BUSY_SQL_PASSWORD=<the password above>                                     */
