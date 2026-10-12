import argparse
import hashlib
import json
import mimetypes
from pathlib import Path

from rocrate.rocrate import ROCrate


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


parser = argparse.ArgumentParser(description="Export a compiled LaTeX source as RO-Crate")
parser.add_argument("--name", required=True)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--pdf", type=Path, required=True)
parser.add_argument("--receipt", type=Path, required=True)
parser.add_argument("--includes", type=Path, required=True, help="JSON {relative path: store path of the derivation output it was copied from}")
parser.add_argument("--origins", type=Path, required=True, help="TSV: relative path<TAB>store path each asset file was copied from")
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

includes = json.loads(args.includes.read_text())
for line in args.origins.read_text().splitlines():
    if line.strip():
        relative, origin = line.split("\t", 1)
        includes[relative] = origin
crate = ROCrate(gen_preview=True)
crate.root_dataset["name"] = args.name
crate.root_dataset["description"] = (
    "Native LaTeX source and its compiled PDF. Files copied from analyses name "
    "their store path in isBasedOn. The build receipt records render provenance "
    "only; this crate does not verify scientific methods or claims."
)
source_dataset = crate.add_jsonld({
    "@id": "source/",
    "@type": "Dataset",
    "name": "Dereferenced LaTeX source tree",
    "description": "Files copied from the exact source tree used for the recorded compilation.",
})
files = []
for path in sorted(args.source.rglob("*")):
    if not path.is_file():
        continue
    relative = path.relative_to(args.source)
    properties = {"name": str(relative), "sha256": sha256(path)}
    if str(relative) in includes:
        properties["isBasedOn"] = includes[str(relative)]
    media_type, _ = mimetypes.guess_type(path.name)
    if media_type:
        properties["encodingFormat"] = media_type
    entity = crate.add_file(
        path,
        dest_path=str(Path("source") / relative),
        properties=properties,
        record_size=True,
    )
    files.append(entity)
source_dataset["hasPart"] = [{"@id": entity.id} for entity in files]

pdf = crate.add_file(args.pdf, dest_path=args.pdf.name, properties={
    "name": args.pdf.name,
    "encodingFormat": "application/pdf",
    "sha256": sha256(args.pdf),
}, record_size=True)
receipt = crate.add_file(args.receipt, dest_path="render-receipt.json", properties={
    "name": "Successful compiler invocation receipt",
    "encodingFormat": "application/json",
    "sha256": sha256(args.receipt),
}, record_size=True)
compiler = crate.add_jsonld({
    "@id": "https://ctan.org/pkg/latexmk",
    "@type": "SoftwareApplication",
    "name": "latexmk",
})
action = crate.add_jsonld({
    "@id": "#latex-compilation",
    "@type": "CreateAction",
    "name": "Compile the exported LaTeX source",
    "description": "A successful Nix build ran the command recorded in render-receipt.json.",
    "instrument": {"@id": compiler.id},
    "object": [{"@id": source_dataset.id}],
    "result": [{"@id": pdf.id}, {"@id": receipt.id}],
    "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"},
})
crate.root_dataset.append_to("hasPart", source_dataset)
crate.root_dataset.append_to("mentions", action)
crate.write(args.out)

# Re-open the written crate and verify every file hash before returning it.
written = ROCrate(args.out)
for entity in written.data_entities:
    props = entity.properties()
    if "sha256" in props:
        file_path = args.out / entity.id
        if sha256(file_path) != props["sha256"]:
            raise ValueError(f"RO-Crate file hash mismatch: {entity.id}")
if not (args.out / "ro-crate-preview.html").is_file():
    raise FileNotFoundError(args.out / "ro-crate-preview.html")
