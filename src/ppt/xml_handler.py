import os
import zipfile
import shutil
from pathlib import Path

def extract_xml_from_pptx(pptx_filepath, output_folder):
    """
    Extracts all constituent XML files from a .pptx file.
    Returns a list of full paths to the extracted XML files.
    """
    extracted_files_paths = []
    try:
        Path(output_folder).mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pptx_filepath, 'r') as pptx_zip:
            for member_info in pptx_zip.infolist():
                member_name = member_info.filename
                if not member_info.is_dir() and member_name.endswith(('.xml', '.rels')):
                    target_path = os.path.join(output_folder, member_name)
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with pptx_zip.open(member_name) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                    extracted_files_paths.append(target_path)
        return extracted_files_paths
    except Exception as e:
        print(f"Error extracting XML from {pptx_filepath}: {e}")
        raise

def create_modified_pptx(original_pptx_path, modified_xml_map, output_pptx_path):
    """
    Creates a new .pptx file by taking an original .pptx, and either updating
    existing internal XML files or adding new ones based on the modified_xml_map.
    """
    temp_output_pptx_path = output_pptx_path + ".tmp"
    try:
        os.makedirs(os.path.dirname(output_pptx_path), exist_ok=True)
        
        # Keep track of which files from the modification map we have used.
        processed_map_files = set()

        with zipfile.ZipFile(original_pptx_path, 'r') as zin:
            with zipfile.ZipFile(temp_output_pptx_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                # 1. Iterate through existing files in the original PPTX.
                for item in zin.infolist():
                    item_name_normalized = item.filename.replace("\\", "/")
                    if item_name_normalized in modified_xml_map:
                        # If an existing file is in our map, write the modified content.
                        new_content = modified_xml_map[item_name_normalized]
                        zout.writestr(item, new_content.encode('utf-8'))
                        processed_map_files.add(item_name_normalized)
                    else:
                        # Otherwise, copy the original file as-is.
                        buffer = zin.read(item.filename)
                        zout.writestr(item, buffer)
                
                # 2. Iterate through the map to find any *new* files to add.
                for filename, content in modified_xml_map.items():
                    if filename not in processed_map_files:
                        print(f"Adding new file to PPTX archive: {filename}")
                        # writestr can take a ZipInfo object or a string filename.
                        zout.writestr(filename, content.encode('utf-8'))

        os.replace(temp_output_pptx_path, output_pptx_path)
        print(f"Modified PPTX successfully created at: {output_pptx_path}")
        return True
    except Exception as e:
        print(f"Error creating modified PPTX at {output_pptx_path}: {e}")
        if os.path.exists(temp_output_pptx_path):
            os.remove(temp_output_pptx_path)
        return False

def extract_specific_xml_from_pptx(pptx_filepath, xml_filename):
    """
    Extracts the content of a single specified XML file from a .pptx file.
    
    Args:
        pptx_filepath (str): Path to the .pptx file.
        xml_filename (str): The internal path to the XML file (e.g., 'ppt/slides/slide1.xml').
        
    Returns:
        str: The content of the XML file as a string, or None if not found.
    """
    try:
        with zipfile.ZipFile(pptx_filepath, 'r') as pptx_zip:
            # Normalize filename for matching
            xml_filename_normalized = xml_filename.replace("\\", "/")
            if xml_filename_normalized in pptx_zip.namelist():
                with pptx_zip.open(xml_filename_normalized) as xml_file:
                    return xml_file.read().decode('utf-8')
            return None
    except Exception as e:
        print(f"Error extracting specific XML '{xml_filename}' from {pptx_filepath}: {e}")
        return None
