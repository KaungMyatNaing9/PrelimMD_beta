import hashlib, time
from pathlib import Path
import fitz
from app.schemas.form_understanding import DetectedFormField, FormAnalysis, RequiredStatus
from app.schemas.models import BoundingBox, FieldType
from app.services.canonical_registry import match, norm

HEADINGS={"patient intake form","prelimmd medical center","demographics","emergency contact","insurance information","medical history","consent"}
def box(words,w,h):
    return BoundingBox(x=min(x[0] for x in words)/w,y=min(x[1] for x in words)/h,width=(max(x[2] for x in words)-min(x[0] for x in words))/w,height=(max(x[3] for x in words)-min(x[1] for x in words))/h)
def normalize(v,t):
    if t==FieldType.DATE:
        import datetime
        try:return datetime.datetime.strptime(v,"%B %d, %Y").date().isoformat()
        except ValueError:return v
    if t==FieldType.PHONE:return "".join(c for c in v if c.isdigit())
    return " ".join(v.split())
class FormAnalyzer:
 def analyze(self,analysis_id:str,folder:Path,template_hint:str|None=None)->FormAnalysis:
    started=time.monotonic(); pdf=folder/"source.pdf"
    if not pdf.exists(): return FormAnalysis(analysis_id=analysis_id,source_type="image",pages=[{"page_index":0,"image_url":f"/api/v1/form-analyses/{analysis_id}/pages/0/image"}],detected_fields=[],form_fingerprint="image",warnings=["Generic image OCR is not available in this bounded PDF analyzer."],processing_time_ms=0)
    doc=fitz.open(pdf); page=doc[0]; pix=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False); pix.save(folder/"normalized_color.png")
    words=page.get_text("words"); w,h=page.rect.width,page.rect.height; groups={}
    for word in words: groups.setdefault((word[5], round(word[1], 1)), []).append(word)
    fields=[]
    for i,line in enumerate(groups.values()):
      line=sorted(line,key=lambda z:z[0]); texts=[z[4] for z in line]; joined=" ".join(texts)
      if ":" not in joined or norm(joined.split(":",1)[0]) in HEADINGS: continue
      colon=next(j for j,t in enumerate(texts) if ":" in t); label=" ".join(texts[:colon+1]).rstrip(":"); answer=" ".join(texts[colon+1:]) or None
      mapped,score,reason=match(label); typ=mapped.value_type if mapped else FieldType.SHORT_TEXT; lb=box(line[:colon+1],w,h)
      ab=box(line[colon+1:],w,h) if answer else BoundingBox(x=min(.98,lb.x+lb.width+.05),y=lb.y,width=max(.02,.92-lb.x-lb.width),height=lb.height)
      fields.append(DetectedFormField(field_id=f"field_{i}",page_index=0,label_text=label,normalized_label=norm(label),field_type=typ,label_bbox=lb,answer_bbox=ab,required_status=RequiredStatus.UNKNOWN,required_confidence=.3,detection_confidence=.95,source="pdf_text",canonical_key=mapped.canonical_key if mapped else None,mapping_confidence=score,mapping_reason=reason,raw_value=answer,normalized_value=normalize(answer,typ) if answer else None,is_blank=answer is None,confidence=.92 if answer else .6,needs_review=not bool(mapped) or answer is None))
    return FormAnalysis(analysis_id=analysis_id,source_type="digital_flat_pdf",pages=[{"page_index":0,"image_url":f"/api/v1/form-analyses/{analysis_id}/pages/0/image","pdf_word_count":len(words),"width":w,"height":h}],detected_fields=fields,form_fingerprint=hashlib.sha256(pdf.read_bytes()).hexdigest(),warnings=[],processing_time_ms=int((time.monotonic()-started)*1000))
