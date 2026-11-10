from pathlib import Path
import hashlib
import json

ITEMS = Path(r"C:\EnnoSmartData\storage\power_automate_import\items")
STAGING = Path(r"C:\EnnoSmartData\storage\power_automate_import\staging")
SOURCE = Path(r"C:\Users\dell\OneDrive - Ennodev\ENNODEV - Clients")

def sha256(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

# On garde le dernier audit connu pour chaque SHA
records = {}

for item in ITEMS.rglob("*.json"):
    try:
        data = json.loads(item.read_text(encoding="utf-8"))
    except Exception:
        continue

    digest = str(data.get("sha256") or "").lower().strip()
    if not digest:
        continue

    old = records.get(digest)
    if old is None or str(data.get("scan_id","")) > str(old.get("scan_id","")):
        records[digest] = data

safe = []
review = []

safe_bytes = 0
review_bytes = 0

files = [p for p in STAGING.rglob("*") if p.is_file()]

print(f"Fichiers staging : {len(files)}")

for i, staged in enumerate(files, 1):
    try:
        size = staged.stat().st_size

        # structure staging : ...\<sha>\<filename>
        digest = staged.parent.name.lower()

        record = records.get(digest)

        if not record:
            review.append((staged, size, "NO_AUDIT_RECORD"))
            review_bytes += size
            continue

        source_path = str(record.get("source_path") or "").strip()

        if not source_path:
            review.append((staged, size, "NO_SOURCE_PATH"))
            review_bytes += size
            continue

        source = SOURCE / Path(source_path)

        if not source.is_file():
            review.append((staged, size, "SOURCE_NOT_FOUND"))
            review_bytes += size
            continue

        expected_size = int(record.get("size") or 0)

        if expected_size and source.stat().st_size != expected_size:
            review.append((staged, size, "SOURCE_SIZE_CHANGED"))
            review_bytes += size
            continue

        # Le staging doit déjà correspondre au SHA du JSON
        staging_sha = sha256(staged)

        if staging_sha != digest:
            review.append((staged, size, "STAGING_SHA_MISMATCH"))
            review_bytes += size
            continue

        # Lecture seule de OneDrive : aucune modification
        source_sha = sha256(source)

        if source_sha == digest:
            safe.append(staged)
            safe_bytes += size
        else:
            review.append((staged, size, "SOURCE_SHA_CHANGED"))
            review_bytes += size

        if i % 250 == 0:
            print(f"{i} fichiers vérifiés...")

    except Exception as exc:
        review.append((staged, 0, f"ERROR: {exc}"))

print("=" * 72)
print(f"SAFE_TO_DELETE : {len(safe)} fichiers / {safe_bytes / 1024**3:.2f} Go")
print(f"NEEDS_REVIEW   : {len(review)} fichiers / {review_bytes / 1024**3:.2f} Go")
print("=" * 72)

from collections import Counter
reasons = Counter(reason for _,_,reason in review)

print("Raisons NEEDS_REVIEW :")
for reason, count in reasons.most_common():
    print(f"{reason:30} {count}")

report = Path(r"C:\EnnoSmartData\logs\staging_onedrive_safe.txt")
report.parent.mkdir(parents=True, exist_ok=True)

with report.open("w", encoding="utf-8") as f:
    for p in safe:
        f.write(str(p) + "\n")

print()
print("Liste SAFE :", report)
print("DRY-RUN : aucun fichier supprimé.")
