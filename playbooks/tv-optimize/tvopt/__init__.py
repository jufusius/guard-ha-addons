"""Guard playbook „tv-optimize“: zrychlení TV, méně reklam a sledování, Kodi, úklid a vrácení změn.

Knihovna je čistá logika bez síťového přístupu. Všechno, co sahá na zařízení, jde přes
rozhraní `GuardApi` (viz engine.py), které implementuje Guard a které vynucuje oddělení
zákazníků (cid) a write-whitelist (CustomerEntityMapping.IsWritable).
"""

MODULES = ("speed", "ads", "kodi", "cleanup")
