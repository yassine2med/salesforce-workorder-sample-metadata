import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from sfmeta.checker import check_field, check_project
from sfmeta.cli import main

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = "force-app/main/default"
OBJ = f"{DEFAULT}/objects/Facility_Work_Order__c"


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "p"
    shutil.copytree(ROOT / "force-app", dest / "force-app")
    shutil.copy(ROOT / "sfdx-project.json", dest / "sfdx-project.json")
    return dest


def messages(path):
    return [str(i) for i in check_project(path)[0]]


def edit(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def field(xml_body: str, stem: str = "X__c"):
    return ET.fromstring(f'<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">{xml_body}</CustomField>'), stem


def test_shipped_project_is_clean():
    issues, inv = check_project(ROOT)
    assert issues == []
    assert inv["files"] == 14 and len(inv["objects"]["Facility_Work_Order__c"]) == 9


def test_missing_project_file(tmp_path):
    assert check_project(tmp_path)[0][0].message == "file not found"


def test_bad_api_version(project):
    edit(project / "sfdx-project.json", '"61.0"', '"61"')
    assert any("sourceApiVersion" in m for m in messages(project))


def test_missing_package_path(project):
    edit(project / "sfdx-project.json", '"force-app"', '"nowhere"')
    assert any("does not exist" in m for m in messages(project))


def test_malformed_xml(project):
    p = project / OBJ / "fields/Due_Date__c.field-meta.xml"
    p.write_text("<CustomField><fullName>oops</CustomField>", encoding="utf-8")
    assert any("not well formed" in m for m in messages(project))


def test_wrong_root_element(project):
    edit(project / OBJ / "fields/Due_Date__c.field-meta.xml", "CustomField", "CustomObject")
    assert any("expected CustomField" in m for m in messages(project))


def test_missing_namespace(project):
    edit(project / OBJ / "fields/Due_Date__c.field-meta.xml", ' xmlns="http://soap.sforce.com/2006/04/metadata"', "")
    assert any("namespace" in m for m in messages(project))


def test_field_name_mismatch(project):
    edit(project / OBJ / "fields/Due_Date__c.field-meta.xml", "<fullName>Due_Date__c", "<fullName>Due_Dates__c")
    assert any("does not match file name" in m for m in messages(project))


def test_field_missing_suffix_and_long_name():
    assert "custom field API name must end with __c" in check_field(*field("<fullName>Bad</fullName><label>a</label><type>Date</type>", "Bad"))
    long = "A" * 41
    assert any("40" in m for m in check_field(*field(f"<fullName>{long}__c</fullName><label>a</label><type>Date</type>", long + "__c")))


def test_text_needs_length():
    out = check_field(*field("<fullName>X__c</fullName><label>a</label><type>Text</type>"))
    assert any("length" in m for m in out)


def test_number_scale_bigger_than_precision():
    out = check_field(*field("<fullName>X__c</fullName><label>a</label><type>Number</type><precision>3</precision><scale>5</scale>"))
    assert any("precision" in m for m in out)


def test_lookup_needs_reference():
    out = check_field(*field("<fullName>X__c</fullName><label>a</label><type>Lookup</type>"))
    assert any("referenceTo" in m for m in out)


def test_checkbox_needs_default():
    out = check_field(*field("<fullName>X__c</fullName><label>a</label><type>Checkbox</type>"))
    assert any("defaultValue" in m for m in out)


def test_duplicate_picklist_value(project):
    edit(project / OBJ / "fields/Priority__c.field-meta.xml", "<fullName>Low</fullName>", "<fullName>High</fullName>")
    assert any("duplicate values" in m for m in messages(project))


def test_validation_rule_unknown_field(project):
    edit(project / OBJ / "validationRules/Due_Date_After_Request.validationRule-meta.xml", "Requested_On__c", "Ghost__c")
    assert any("unknown field Facility_Work_Order__c.Ghost__c" in m for m in messages(project))


def test_permission_set_unknown_and_required_field(project):
    p = project / f"{DEFAULT}/permissionsets/Facility_Work_Order_Manager.permissionset-meta.xml"
    edit(p, "Facility_Work_Order__c.Status__c", "Facility_Work_Order__c.Priority__c")
    msgs = messages(project)
    assert any("is required" in m for m in msgs)
    edit(p, "Facility_Work_Order__c.Due_Date__c", "Facility_Work_Order__c.Nope__c")
    assert any("unknown field Facility_Work_Order__c.Nope__c" in m for m in messages(project))


def test_flow_unknown_field_and_broken_connector(project):
    p = project / f"{DEFAULT}/flows/Set_Default_Work_Order_Status.flow-meta.xml"
    edit(p, "$Record.Status__c", "$Record.Status_Typo__c")
    assert any("Status_Typo__c" in m for m in messages(project))
    edit(p, "<targetReference>Set_Status_New", "<targetReference>Missing_Step")
    assert any("missing element 'Missing_Step'" in m for m in messages(project))


def test_list_view_unknown_column(project):
    edit(project / OBJ / "listViews/Open_Work_Orders.listView-meta.xml", "<columns>Priority__c", "<columns>Prio__c")
    assert any("Prio__c" in m for m in messages(project))


def test_field_without_object_definition(project):
    (project / OBJ / "Facility_Work_Order__c.object-meta.xml").unlink()
    assert any("no object definition" in m for m in messages(project))


def test_cli_exit_codes_and_output(capsys):
    assert main(["check", str(ROOT)]) == 0
    assert "14 metadata files checked, 0 issue(s)" in capsys.readouterr().out
    assert main(["check", str(ROOT / "examples" / "broken-project")]) == 1
    assert main(["check", str(ROOT / "missing")]) == 2
    assert main(["inventory", str(ROOT)]) == 0
    assert "9 custom field(s)" in capsys.readouterr().out
