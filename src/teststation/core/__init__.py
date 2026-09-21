"""node 基底與全站共通契約。

這裡**刻意不做轉出**:要用什麼就從子模組直接拿(`from .core.node import Node`)。
轉出一層的代價是 import 一個東西會把整包拖進來 —— 例如想用 `topics` 的純函式
卻連 eCAL 一起載入。
"""
