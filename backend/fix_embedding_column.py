"""One-time fix: an older database has guideline_chunks.embedding as jsonb, but the RAG code stores real[].
Run once from the backend folder:   python fix_embedding_column.py      then:   python load_guidelines.py
Only changes the column when the table is empty, so it cannot destroy loaded guideline text."""
import db

row = db.one("SELECT data_type FROM information_schema.columns WHERE table_name='guideline_chunks' AND column_name='embedding'")
print("embedding column type:", row and row["data_type"])
if row is None:
    print("No embedding column yet. Nothing to fix; load_guidelines.py will add it.")
elif row["data_type"] == "ARRAY":
    print("Already correct (real[]). Nothing to do.")
else:
    n = db.one("SELECT count(*) AS n FROM guideline_chunks")["n"]
    print("rows in guideline_chunks:", n)
    if n == 0:
        db.run("ALTER TABLE guideline_chunks DROP COLUMN embedding")
        db.run("ALTER TABLE guideline_chunks ADD COLUMN embedding real[]")
        print("Fixed: embedding is now real[]. Now run: python load_guidelines.py")
    else:
        print("The table already has rows, so I did not change it. Send this message to Claude.")