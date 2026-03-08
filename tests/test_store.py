from __future__ import annotations

from codexaudit.indexer.store import IndexStore
from codexaudit.models import FileInfo, Language


def test_prune_missing_files_removes_stale_rows(tmp_path) -> None:
    store = IndexStore(tmp_path / ".codexaudit" / "codexaudit.db")
    store.initialize()
    store.insert_file(FileInfo(path="keep.py", language=Language.PYTHON, size=0))
    store.insert_file(FileInfo(path="drop.py", language=Language.PYTHON, size=0))

    pruned = store.prune_missing_files({"keep.py"})

    assert pruned == 1
    assert store.get_file_count() == 1
    assert store.get_file_by_path("keep.py") is not None
    assert store.get_file_by_path("drop.py") is None
    store.close()
