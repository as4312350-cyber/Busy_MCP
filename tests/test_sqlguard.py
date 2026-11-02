import pytest

from busy_mcp.sqlguard import UnsafeQueryError, validate_select


@pytest.mark.parametrize("sql", [
    "SELECT * FROM Master1",
    "select top 5 Name from Master1 where Name = 'DELETE ME; DROP'",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT [Update], REPLACE(Name,'a','b') FROM Master1;",
    "SELECT Name -- drop table\nFROM Master1",
])
def test_allowed(sql):
    assert validate_select(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM Master1",
    "UPDATE Master1 SET Name='x'",
    "SELECT * INTO Copy FROM Master1",
    "SELECT 1; DROP TABLE Master1",
    "EXEC xp_cmdshell 'dir'",
    "SELECT * FROM OPENROWSET('x','y','z')",
    "WITH a AS (SELECT 1) DELETE FROM Master1",
    "SELECT 1 /* unterminated",
    "  ",
    "INSERT INTO Master1 VALUES (1)",
    "SELECT sp_who FROM t",
])
def test_blocked(sql):
    with pytest.raises(UnsafeQueryError):
        validate_select(sql)
