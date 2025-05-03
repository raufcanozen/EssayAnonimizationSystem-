"""
Blurring Capability Code
Author: Rauf Özen
Date Finished: March 23, 2025

An extension of the PDFAnonymizer that adds blurring capabilities for images
and text replacement for sensitive content in academic papers.
"""

import os
import re
import io
import fitz #PyMuPDF
import spacy
import logging
import cv2
import numpy as np
from PIL import Image
import tempfile

#Configured the logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
"""
IMPORTANT NOTE: At first I thought the papers would be in English and Turkish, 
so when prepearing the regex I have put some Turkish key words as well, and after ensuring it works,
I learned that the papers would be in English. 
This code works well and it working with Turkish is an extra capability. So, I decided to keep it.
"""
#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

class PDFBlurAnonymizer: 
    def __init__(self):
        """Initialize anonymizer with NLP models and patterns"""
        #Loaded the NLP model
        try:
            self.nlp = spacy.load("en_core_web_sm")
            logger.info("Loaded English NLP model")
        except:
            logger.warning("English model not found, downloading...")
            spacy.cli.download("en_core_web_sm")
            self.nlp = spacy.load("en_core_web_sm")
        #Stored the sections to skip for anonymization.
        self.skip_sections = [
            "introduction", "giriş", 
            "related work", "ilgili çalışmalar",
            "references", "referanslar", "kaynakça",
            "acknowledgement", "acknowledgments", "teşekkür"
        ]
        
        #Initialized the patterns for regex (Regular Expression)
        self.email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
        
        #Mapped to store original text for replacement.
        self.anonymization_map = {
            'persons': {},
            'emails': {},
            'institutions': {},
        }
        
        # Counters for replacements
        self.counters = {
            'persons': 1,
            'emails': 1,
            'institutions': 1
        }
        
        # Parameters for blurring
        self.blur_kernel_size = 25
        
    def detect_sections(self, text):
        """Detect different sections in the document"""
        lines =text.split('\n')
        sections = {}
        current_section = "header"
        content= ""
        
        # Common headers in academic papers
        section_pattern = re.compile(
            r'^(?:\d+\.?)?(?:\s+)?(abstract|özet|introduction|giriş|related work|ilgili çalışmalar|'
            r'methodology|yöntem|results|sonuçlar|discussion|tartışma|conclusion|sonuç|'
            r'references|referanslar|kaynakça|acknowledgement|acknowledgments|teşekkür)', 
            re.IGNORECASE
        )
        for line in lines:
            line = line.strip()
            match = section_pattern.match(line)
            if match:
                #Save the previous section.
                if content:
                    sections[current_section] = content
                #Started a new section.
                current_section = match.group(1).lower()
                content = line + "\n"
            else:
                content += line + "\n"
        
        #Added the last section.
        if content:
            sections[current_section] = content
        
        return sections

    def extract_authors_from_header(self, text):
        """Extracted the author names from the paper header."""
        header_text = text[:1000] if len(text) > 1000 else text
        authors = set()

        #First: Take the names using spaCy
        doc = self.nlp(header_text)
        for ent in doc.ents:
            if ent.label_ == "PERSON" and len(ent.text.split()) >= 2:
                authors.add(ent.text.strip())

        # Second: Names matched using superscript
        superscript_pattern = re.compile(
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)(?:[¹²³⁴⁵⁶⁷⁸⁹\*†‡§]|\[\d+\])'
        )
        for match in superscript_pattern.finditer(header_text):
            authors.add(match.group(1).strip())

        # Third: Corresponding authors
        corr_author_pattern = re.compile(
            r'[Cc]orresponding\s+[Aa]uthor\s*:\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)'
        )
        for match in corr_author_pattern.finditer(text):
            authors.add(match.group(1).strip())

        # Fourth: Email contextes.
        for email in self.email_pattern.findall(header_text):
            email_context = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*\(' + re.escape(email) + r'\)', text)
            if email_context:
                authors.add(email_context.group(1).strip())

        # Fifth: Line based control: Name + departments
        lines = header_text.splitlines()
        for i in range(len(lines) - 1):
            name_candidate = lines[i].strip()
            next_line = lines[i + 1].strip()
            if name_candidate in authors:
                continue
            if re.match(r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+$', name_candidate):
                if re.search(r'\b(Department|Faculty|University|Institute|School|College)\b', next_line, re.IGNORECASE):
                    authors.add(name_candidate)
        return list(authors)

    def extract_institutions(self, text):
        """Extract the names of the institutions from the paper header"""
        #Focus where institution info typically appears.
        header_text = text[:2000] if len(text) > 2000 else text
        institutions = set()
        
        #Looked for formal institution patterns.
        institution_pattern = re.compile(
            r'([A-Za-z\s]+(?:University|Institute|College|Department|School|Faculty)(?:\s+of\s+[A-Za-z\s]+)?)',
            re.IGNORECASE
        )
        
        for match in institution_pattern.finditer(header_text):
            institution = match.group(1).strip()
            if len(institution) > 5:  #Avoid very short matches
                institutions.add(institution)
        #Look for organizations using spaCy.
        doc = self.nlp(header_text)
        for ent in doc.ents:
            if ent.label_ == "ORG" and ("university" in ent.text.lower() or "institute" in ent.text.lower()):
                institutions.add(ent.text.strip())
        return list(institutions)
    
    def should_skip_section(self, section_name):
        """Checkd if the section should be skipped for anonymization"""
        section_name = section_name.lower()
        for skip_section in self.skip_sections:
            if skip_section in section_name:
                return True
        return False

    def blur_image(self, pix):
        """Apply Gaussian blur to a pixmap image"""
        #Converted pixmap to numpy array.
        img_bytes = pix.tobytes("png")
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        #Applied the Gaussian blur.
        blurred = cv2.GaussianBlur(img, (self.blur_kernel_size, self.blur_kernel_size), 0)
        #Encodes back to bytes.
        _, buf = cv2.imencode('.png', blurred)
        img_bytes = buf.tobytes()
        #Created a new pixmap from the blurred image.
        return fitz.Pixmap(img_bytes)

    def is_likely_author_image(self, pix, page_text):
        """Determined if an image is likely to be an author photo"""
        #Checked for image dimensions (author photos are usually small and square-ish)
        width, height = pix.width, pix.height
        aspect_ratio = width / height if height > 0 else 0
        
        #Most of the author photos in academic papers are relatively small and somewhat square.
        size_check = width < 500 and height < 500
        ratio_check = 0.7 < aspect_ratio < 1.4
        
        #Looked for author names near the image.
        author_nearby = False
        for author in self.anonymization_map['persons'].keys():
            if author in page_text[:2000]:  #Checked in first part of page
                author_nearby = True
                break
        return True
        
    def create_anonymization_map(self, pdf_path):
        """Analyzeed the PDF and create anonymization map"""
        doc = fitz.open(pdf_path)
        
        #Extracted full text for analysis
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        doc.close()
        
        #Extracted the authors, institutions and emails.
        authors = self.extract_authors_from_header(full_text)
        institutions = self.extract_institutions(full_text)
        emails = self.email_pattern.findall(full_text)
        
        #Created the anonymization mappings.
        for author in authors:
            self.anonymization_map['persons'][author] = f"AUTHOR-{self.counters['persons']}"
            self.counters['persons'] += 1
        
        for institution in institutions:
            self.anonymization_map['institutions'][institution] = f"INSTITUTION-{self.counters['institutions']}"
            self.counters['institutions'] += 1
        
        for email in emails:
            self.anonymization_map['emails'][email] = f"EMAIL-{self.counters['emails']}"
            self.counters['emails'] += 1
        
        #Displayed the detected entities.
        logger.info(f"Found {len(self.anonymization_map['persons'])} persons:")
        for original, anonymized in self.anonymization_map['persons'].items():
            logger.info(f"  {original} -> {anonymized}")
        
        logger.info(f"Found {len(self.anonymization_map['institutions'])} institutions:")
        for original, anonymized in self.anonymization_map['institutions'].items():
            logger.info(f"  {original} -> {anonymized}")
        
        logger.info(f"Found {len(self.anonymization_map['emails'])} emails:")
        for original, anonymized in self.anonymization_map['emails'].items():
            logger.info(f"  {original} -> {anonymized}")
        
        return self.anonymization_map

    def anonymize_pdf(self, input_pdf_path, output_pdf_path):
        
        logger.info(f"Anonymizing PDF: {input_pdf_path}")
        
        try:
            #I Reset the anonymization map for each new PDF.
            self.anonymization_map = {
                'persons': {},
                'emails': {},
                'institutions': {},
            }
            self.counters = {
                'persons': 1,
                'emails': 1,
                'institutions': 1
            }
            #Created the anonymization map for this PDF
            self.create_anonymization_map(input_pdf_path)
            #Copied the input PDF to a temp file for processing
            temp_output = tempfile.mktemp(suffix='.pdf')

            doc = fitz.open(input_pdf_path)
            
            #Processwd each page of the PDF
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_text = page.get_text()
                
                #Detected the sections for this page to determine if we should skip.
                sections = self.detect_sections(page_text)
                skip_anonymization = False
                for section_name in sections.keys():
                    if self.should_skip_section(section_name):
                        skip_anonymization = True
                        break
                if skip_anonymization:
                    logger.info(f"Skipping anonymization for page {page_num+1}")
                    continue
                
                #Blur author photos
                image_list = page.get_images(full=True)
                for img_index, img_info in enumerate(image_list):
                    xref = img_info[0]  
                    try:
                        pix = fitz.Pixmap(doc, xref)
                        # If the pixmap has alpha channel and isn't one of the standard types, skip it.
                        if pix.n > 4:
                            continue
                        # If pixmap has an alpha, remove it for processing.
                        if pix.alpha:
                            pix_no_alpha = fitz.Pixmap(fitz.csRGB, pix)
                        else:
                            pix_no_alpha = pix
                        #Checked if image needs blurring.
                        if self.is_likely_author_image(pix_no_alpha, page_text):
                            logger.info(f"Blurring likely author image on page {page_num+1}")
                            #Blured the image if it needs it
                            blurred_pix = self.blur_image(pix_no_alpha)
                            #Saved it to the temporary file.
                            temp_img_path = tempfile.mktemp(suffix='.png')
                            blurred_pix.save(temp_img_path)
                            
                            #Got the image rectangle on the page.
                            image_rects = page.get_image_rects(xref)
                            if image_rects:
                                rect = image_rects[0]  #Use the first occurrence.
                                
                                #Removed the old image.
                                page.add_redact_annot(rect, fill=(1, 1, 1))
                                page.apply_redactions()
                                
                                #Inserted the blurred image.
                                page.insert_image(rect, filename=temp_img_path)
                                
                            #Clean up part.
                            os.remove(temp_img_path)
                            
                            if pix.alpha and pix is not pix_no_alpha:
                                pix_no_alpha = None  # Freed the pixmap.
                    except Exception as e:
                        logger.error(f"Error processing image {xref}: {str(e)}")
                
                #Anonymizing the text content: Redacting and replacing
                for entity_type, replacements in self.anonymization_map.items():
                    for original_text, replacement_text in replacements.items():
                        #Find all occurrences of the text on this page.
                        instances = page.search_for(original_text)
                        
                        #Added the redaction annotations.
                        for rect in instances:
                            annot = page.add_redact_annot(rect, text=replacement_text)
                
                #Applirf all of theredactions at once.
                page.apply_redactions()
            
            #Saved the modified document.
            doc.save(temp_output, garbage=4, deflate=True, clean=True)
            doc.close()
            
            #Copied the temp file to the output path (instead of moving, which fails across drives)
            import shutil
            if os.path.exists(output_pdf_path):
                os.remove(output_pdf_path)
            shutil.copy2(temp_output, output_pdf_path)
            os.remove(temp_output)  #Cleaned the temp file
            logger.info(f"Anonymized PDF saved as: {output_pdf_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error anonymizing PDF: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def restore_original_info(self, anonymized_pdf_path, output_pdf_path, info_to_restore=None):
        """Restore selected original information to an anonymized PDF"""
        logger.info(f"Restoring selected information to: {anonymized_pdf_path}")
        
        if not info_to_restore:
            info_to_restore = []  #Nothing to restore.
        
        try:
            #Used a temporary file for processing.
            temp_output = tempfile.mktemp(suffix='.pdf')
            
            #Opened the anonymized PDF.
            doc = fitz.open(anonymized_pdf_path)
            
            #For each page, restored the selected information.
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                #Reversed mapping for restoration.
                reverse_map = {}
                for entity_type in info_to_restore:
                    if entity_type in self.anonymization_map:
                        for original, anonymized in self.anonymization_map[entity_type].items():
                            reverse_map[anonymized] = original
                
                #Found and replaced all anonymized texts with originals
                for anonymized, original in reverse_map.items():
                    areas = page.search_for(anonymized)
                    for rect in areas:
                        #Created the redaction annotation with original text.
                        annot = page.add_redact_annot(rect, text=original)
                
                #Applied all of the redactions at once.
                page.apply_redactions()
            
            #Saved the restored document.
            doc.save(temp_output, garbage=4, deflate=True, clean=True)
            doc.close()
            
            #Copied the temp file to the output path(instead of moving, which fails across drives for some reasons)
            import shutil
            if os.path.exists(output_pdf_path):
                os.remove(output_pdf_path)
            shutil.copy2(temp_output, output_pdf_path)
            os.remove(temp_output)#Cleaned up the temp file
            logger.info(f"Restored PDF saved as: {output_pdf_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error restoring information: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

#Main function for demonstrating the usage
def main():
    #Create an anonymizer.
    anonymizer = PDFBlurAnonymizer()
    
    #Process PDF files from command line arguments or use default
    import sys
    
    if len(sys.argv) > 1:
        input_pdf = sys.argv[1]
        output_pdf = input_pdf.replace('.pdf', '_anonymized.pdf')
    else:
        input_pdf = "örnek_makale1.pdf"
        output_pdf = "örnek_makale1_anonymized.pdf"
    
    print(f"Processing: {input_pdf}")
    
    #First analyze the PDF for creating anonymization map.
    anonymization_map = anonymizer.create_anonymization_map(input_pdf)
    
    print("\nAnonymization map created:")
    print(f"  - Authors: {len(anonymization_map['persons'])}")
    print(f"  - Institutions: {len(anonymization_map['institutions'])}")
    print(f"  - Emails: {len(anonymization_map['emails'])}")
    
    #Anonymize the PDF.
    success = anonymizer.anonymize_pdf(input_pdf, output_pdf)
    
    if success:
        print(f"\nSuccessfully anonymized: {output_pdf}")
        # Create a version for editors with institutions restored.
        restored_pdf = output_pdf.replace('.pdf', '_restored.pdf')
        restore_success = anonymizer.restore_original_info(
            output_pdf, 
            restored_pdf,
            info_to_restore=['institutions'] #Only restore institution names
        )
        
        if restore_success:
            print(f"Created version with institutions restored: {restored_pdf}")
    else:
        print("Anonymization failed")

if __name__ == "__main__":
    main()