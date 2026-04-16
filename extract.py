import zipfile
import xml.etree.ElementTree as ET

def extract_text(path):
    try:
        with zipfile.ZipFile(path) as d:
            xml_content = d.read('word/document.xml')
            tree = ET.fromstring(xml_content)
            # Find all text elements
            texts = []
            for node in tree.iter():
                # w:t indicates text
                if node.tag.endswith('}t') and node.text:
                    texts.append(node.text)
                # w:p indicates paragraph, we can add a newline
                elif node.tag.endswith('}p'):
                    texts.append('\n')
            return ''.join(texts)
    except Exception as e:
        return str(e)

with open('plan.txt', 'w') as f:
    f.write(extract_text('1. SIMO_Daily_check_v1.0.docx'))
    f.write('\n\n=============================\n\n')
    f.write(extract_text('SIMO_DailyCheck_AppDev_Plan.docx'))

print("Done")
