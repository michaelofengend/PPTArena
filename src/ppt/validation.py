from typing import Optional

def validate_xml(xml_text):
    """Return True if xml_text is well-formed."""
    try:
        from lxml import etree
        etree.fromstring(xml_text.encode("utf-8"))
        return True
    except Exception as e:
        print(f"Invalid XML detected: {e}")
        return False

def attempt_repair_xml(xml_text: str) -> Optional[str]:
    """
    Attempt to repair malformed XML using lxml's recovery parser.
    Returns a repaired XML string if successful, otherwise None.
    """
    try:
        from lxml import etree
        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(xml_text.encode("utf-8"), parser=parser)
        if root is None:
            return None
        # Serialize back to UTF-8 string
        fixed = etree.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        return fixed
    except Exception as e:
        print(f"XML repair failed: {e}")
        return None
