from __future__ import annotations

import pytest

from busy_mcp.errors import UnsafeQueryError
from busy_mcp.sqlguard import validate_select


@pytest.mark.parametrize("sql", [
    "SELECT * FROM Master1",
    "select top 5 Name from Master1 where Name = 'DELETE ME; DROP'",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT [Update], REPLACE(Name,'a','b') FROM Master1;",
    "SELECT Name -- drop table\nFROM Master1",
    "SELECT * FROM Tran1 WHERE VchType IN (9, 3)",
    "SELECT * FROM Master1 WHERE Name IN ('a','b')",
    "SELECT * FROM Tran1 ORDER BY VchCode OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY",
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
    "SELECT * FROM Master1 IN 'C:\\other.mdb'",
    "SELECT * FROM [;DATABASE=C:\\other.mdb].Master1",
    "SELECT * FROM [Text;HDR=Yes;DATABASE=C:\\].[secrets.csv]",
    "SELECT Shell('calc.exe') FROM Master1",
    "SELECT Eval('1') FROM Master1",
    "SELECT NEXT VALUE FOR seq",
    "SELECT 'x" ,
    "SELECT 1\x00",
    "SELECT " + "1," * 10000 + "1",
])
def test_blocked(sql):
    with pytest.raises(UnsafeQueryError):
        validate_select(sql)
