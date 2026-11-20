"""Static checks for Salesforce DX source-format metadata. No org connection is used."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

NS = "http://soap.sforce.com/2006/04/metadata"
ROOT_FOR_SUFFIX = {
    "object": "CustomObject",
    "field": "CustomField",
    "validationRule": "ValidationRule",
    "listView": "ListView",
    "permissionset": "PermissionSet",
    "flow": "Flow",
}
FIELD_TYPES = {"Text", "TextArea", "LongTextArea", "Number", "Currency", "Percent", "Date", "DateTime",
               "Checkbox", "Picklist", "MultiselectPicklist", "Lookup", "MasterDetail", "Email", "Phone", "Url"}
CUSTOM_REF = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*__c)\b")
FILE_PATTERN = re.compile(r"^(?P<name>.+)\.(?P<kind>[A-Za-z]+)-meta\.xml$")


@dataclass(frozen=True)
class Issue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    for c in node:
        if _local(c.tag) == name:
            return c
    return None


def _text(node: ET.Element, name: str) -> str:
    c = _child(node, name)
    return (c.text or "").strip() if c is not None else ""


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in node if _local(c.tag) == name]


def check_field(node: ET.Element, file_stem: str) -> list[str]:
    """Return problems with one CustomField element."""
    out = []
    name = _text(node, "fullName")
    ftype = _text(node, "type")
    if name != file_stem:
        out.append(f"fullName '{name}' does not match file name '{file_stem}'")
    if not name.endswith("__c"):
        out.append("custom field API name must end with __c")
    elif len(name) - 3 > 40:
        out.append("API name is longer than 40 characters")
    if not _text(node, "label"):
        out.append("label is missing")
    if ftype not in FIELD_TYPES:
        out.append(f"type '{ftype}' is missing or not a known field type")
        return out
    if ftype == "Text":
        length = _text(node, "length")
        if not length.isdigit() or not 1 <= int(length) <= 255:
            out.append("Text field needs a length from 1 to 255")
    if ftype == "LongTextArea":
        length = _text(node, "length")
        if not length.isdigit() or not 256 <= int(length) <= 131072:
            out.append("LongTextArea needs a length from 256 to 131072")
        if not _text(node, "visibleLines"):
            out.append("LongTextArea needs visibleLines")
    if ftype in ("Number", "Currency", "Percent"):
        precision, scale = _text(node, "precision"), _text(node, "scale")
        if not precision.isdigit() or not scale.isdigit():
            out.append(f"{ftype} field needs numeric precision and scale")
        elif int(scale) > int(precision) or int(precision) > 18:
            out.append("precision must be at most 18 and not below scale")
    if ftype in ("Picklist", "MultiselectPicklist"):
        values = [_text(v, "fullName") for v in node.iter() if _local(v.tag) == "value" and _child(v, "fullName") is not None]
        if not values:
            out.append("picklist has no values")
        elif len(values) != len(set(values)):
            out.append("picklist has duplicate values")
        defaults = [v for v in node.iter() if _local(v.tag) == "value" and _text(v, "default") == "true"]
        if ftype == "Picklist" and len(defaults) > 1:
            out.append("picklist has more than one default value")
    if ftype in ("Lookup", "MasterDetail"):
        if not _text(node, "referenceTo") or not _text(node, "relationshipName"):
            out.append(f"{ftype} needs referenceTo and relationshipName")
    if ftype == "Checkbox" and _text(node, "defaultValue") not in ("true", "false"):
        out.append("Checkbox needs defaultValue true or false")
    return out


def check_project(root: Path) -> tuple[list[Issue], dict]:
    """Check every metadata file under the package directories of an sfdx project.

    Returns (issues, inventory) where inventory counts files and lists objects
    with their custom fields.
    """
    issues: list[Issue] = []
    project_file = root / "sfdx-project.json"
    if not project_file.exists():
        return [Issue("sfdx-project.json", "file not found")], {}
    try:
        project = json.loads(project_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [Issue("sfdx-project.json", f"not valid JSON: {exc}")], {}
    if not re.fullmatch(r"\d+\.0", str(project.get("sourceApiVersion", ""))):
        issues.append(Issue("sfdx-project.json", "sourceApiVersion must look like 61.0"))
    dirs = project.get("packageDirectories") or []
    if not dirs:
        issues.append(Issue("sfdx-project.json", "packageDirectories is empty"))
    if sum(1 for d in dirs if d.get("default")) != 1 and dirs:
        issues.append(Issue("sfdx-project.json", "exactly one packageDirectory must be default"))

    parsed: dict[Path, tuple[str, str, ET.Element]] = {}
    for d in dirs:
        base = root / d.get("path", "")
        if not base.is_dir():
            issues.append(Issue("sfdx-project.json", f"package path '{d.get('path')}' does not exist"))
            continue
        for path in sorted(base.rglob("*-meta.xml")):
            rel = path.relative_to(root).as_posix()
            m = FILE_PATTERN.match(path.name)
            if not m or m["kind"] not in ROOT_FOR_SUFFIX:
                continue
            try:
                tree = ET.parse(path)
            except ET.ParseError as exc:
                issues.append(Issue(rel, f"XML is not well formed: {exc}"))
                continue
            node = tree.getroot()
            expected = ROOT_FOR_SUFFIX[m["kind"]]
            if _local(node.tag) != expected:
                issues.append(Issue(rel, f"root element is {_local(node.tag)}, expected {expected}"))
                continue
            if not node.tag.startswith("{" + NS + "}"):
                issues.append(Issue(rel, f"root element must use the namespace {NS}"))
            parsed[path] = (m["name"], m["kind"], node)

    objects: dict[str, set[str]] = {}
    required: set[tuple[str, str]] = set()
    for path, (name, kind, node) in parsed.items():
        if kind == "object":
            objects.setdefault(path.parent.name, set())
            if name != path.parent.name:
                issues.append(Issue(path.relative_to(root).as_posix(), "file name does not match its folder"))
            for req in ("label", "pluralLabel", "deploymentStatus", "sharingModel"):
                if not _text(node, req):
                    issues.append(Issue(path.relative_to(root).as_posix(), f"{req} is missing"))
            if _child(node, "nameField") is None:
                issues.append(Issue(path.relative_to(root).as_posix(), "nameField is missing"))
    for path, (name, kind, node) in parsed.items():
        if kind == "field":
            obj = path.parent.parent.name
            if obj not in objects:
                issues.append(Issue(path.relative_to(root).as_posix(), f"no object definition found for folder '{obj}'"))
            objects.setdefault(obj, set()).add(name)
            if _text(node, "required") == "true":
                required.add((obj, name))
            for msg in check_field(node, name):
                issues.append(Issue(path.relative_to(root).as_posix(), msg))

    def unknown(rel: str, obj: str, names: set[str], where: str) -> None:
        for ref in sorted(names):
            if ref not in objects.get(obj, set()):
                issues.append(Issue(rel, f"{where} refers to unknown field {obj}.{ref}"))

    flows = 0
    for path, (name, kind, node) in parsed.items():
        rel = path.relative_to(root).as_posix()
        if kind == "validationRule":
            obj = path.parent.parent.name
            if _text(node, "fullName") != name:
                issues.append(Issue(rel, "fullName does not match file name"))
            for req in ("errorConditionFormula", "errorMessage", "active"):
                if not _text(node, req):
                    issues.append(Issue(rel, f"{req} is missing"))
            unknown(rel, obj, set(CUSTOM_REF.findall(_text(node, "errorConditionFormula"))), "formula")
            shown = _text(node, "errorDisplayField")
            if shown.endswith("__c"):
                unknown(rel, obj, {shown}, "errorDisplayField")
        elif kind == "listView":
            obj = path.parent.parent.name
            if _text(node, "fullName") != name:
                issues.append(Issue(rel, "fullName does not match file name"))
            cols = {c.text.strip() for c in _children(node, "columns") if c.text}
            fields = {_text(f, "field") for f in _children(node, "filters")}
            unknown(rel, obj, {c for c in cols | fields if c.endswith("__c")}, "list view")
        elif kind == "permissionset":
            for fp in _children(node, "fieldPermissions"):
                obj, _, fld = _text(fp, "field").partition(".")
                if obj not in objects:
                    issues.append(Issue(rel, f"field permission names unknown object '{obj}'"))
                elif fld not in objects[obj]:
                    issues.append(Issue(rel, f"field permission names unknown field {obj}.{fld}"))
                elif (obj, fld) in required:
                    issues.append(Issue(rel, f"{obj}.{fld} is required, so it cannot appear in a permission set"))
            for op in _children(node, "objectPermissions"):
                if _text(op, "object") not in objects:
                    issues.append(Issue(rel, f"object permission names unknown object '{_text(op, 'object')}'"))
            if not _text(node, "label"):
                issues.append(Issue(rel, "label is missing"))
        elif kind == "flow":
            flows += 1
            for req in ("apiVersion", "label", "status", "processType"):
                if not _text(node, req):
                    issues.append(Issue(rel, f"{req} is missing"))
            start = _child(node, "start")
            obj = _text(start, "object") if start is not None else ""
            if obj:
                if obj not in objects:
                    issues.append(Issue(rel, f"flow start object '{obj}' is not in this project"))
                else:
                    text = ET.tostring(node, encoding="unicode")
                    refs = set(re.findall(r"\$Record\.([A-Za-z0-9_]+__c)", text))
                    refs |= {_text(f, "field") for f in _children(start, "filters") if _text(f, "field").endswith("__c")}
                    unknown(rel, obj, refs, "flow")
            targets = {_text(c, "targetReference") for c in node.iter() if _local(c.tag) == "connector"}
            names = {_text(c, "name") for c in node if _local(c.tag) in ("assignments", "decisions", "recordCreates",
                                                                           "recordUpdates", "recordLookups", "screens")}
            for t in sorted(targets - names):
                issues.append(Issue(rel, f"connector points to missing element '{t}'"))

    inventory = {
        "files": len(parsed),
        "flows": flows,
        "objects": {o: sorted(f) for o, f in sorted(objects.items())},
    }
    return issues, inventory
