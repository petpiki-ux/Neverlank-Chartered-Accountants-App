import os, sys, tempfile, io, re
os.environ["DATA_DIR"]=tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0,"/home/claude/wd2")
import flask
from flask.testing import FlaskClient
_o=FlaskClient.open
def _open(self,*a,**k):
    flask.g.pop("_login_user",None) if flask.has_app_context() else None
    return _o(self,*a,**k)
FlaskClient.open=_open
from datetime import date
from app import create_app
from extensions import db
from models import *
import financials as fin, asset_recon as R
app=create_app(); app.config["WTF_CSRF_ENABLED"]=False
XL=open("/tmp/claude-0/fa2.xlsx","rb").read()
with app.app_context():
    db.create_all()
    u=User(username="p",name="P",role="partner"); u.set_password("x"); db.session.add(u); db.session.commit()
    cl=Client(name="Oakbrook",created_by_id=u.id); db.session.add(cl); db.session.commit()
    e=Engagement(client_id=cl.id,title="Audit 2025",type="Audit",partner_id=u.id,manager_id=u.id,acceptance_required=False,period_end=date(2025,12,31),reporting_framework="ifrs_for_smes")
    db.session.add(e); db.session.commit(); EID,UID=e.id,u.id
    tb=TrialBalance(engagement_id=EID); db.session.add(tb); db.session.flush()
    def add(n,c,cd=0,cc=0,pd=0,pc=0): db.session.add(TrialBalanceLine(trial_balance_id=tb.id,account_name=n,fs_category=c,current_debit=cd,current_credit=cc,prior_debit=pd,prior_credit=pc))
    add("Property plant and equipment","ppe",cd=901852,pd=901852)
    add("Bank","cash",cd=500000,pd=500000)
    add("Share capital","share_capital",cc=100000,pc=100000)
    add("Retained earnings","retained_earnings",cc=1301852,pc=1301852)
    db.session.add(PPEAssetClass(engagement_id=EID,name="Property plant and equipment",order=1)); db.session.commit()
    ocl=PPEAssetClass.query.first(); db.session.add(PPEAsset(engagement_id=EID,asset_class_id=ocl.id,description="Property plant and equipment",cost=901852.0)); db.session.commit()
c=app.test_client()
with c.session_transaction() as s: s["_user_id"]=str(UID); s["_fresh"]=True
r=c.post(f"/engagements/{EID}/revaluation/import/upload",data={"file":(io.BytesIO(XL),"v.xlsx")},content_type="multipart/form-data")
tok=re.search(r"/import/([0-9a-f]{32})/confirm",r.data.decode()).group(1)
r=c.post(f"/engagements/{EID}/revaluation/import/{tok}/confirm",data={"ext":".xlsx","filename":"v","create_missing":"1","use":"fair_value"},follow_redirects=True)


h=r.data.decode()
for m in re.findall(r'class="flash[^"]*"[^>]*>(.*?)</div>',h,re.S)[:2]: print("FLASH:",re.sub(r"\s+"," ",m)[:900])
import ppe_revaluation as pr
with app.app_context():
    eng=Engagement.query.get(EID); rec=pr.get(EID)
    db.session.commit()
    calc=pr.calculate(eng,rec); print("issues",calc["issues"]); t=calc["totals"]; print("nbv",t["nbv"],"fv",t["fair_value"],"oci",t["oci"],"pl",t["pl"])
h0=c.get(f"/engagements/{EID}?tab=asset_register").data.decode()
assert "Post to Movement Schedule and Financial Statements" in h0, "button missing"
assert 'name="quick"' in h0
r=c.post(f"/engagements/{EID}/revaluation/apply",data={"quick":"1"},follow_redirects=True)
with app.app_context():
    tbr=TrialBalance.query.filter_by(engagement_id=EID).first()
    print([ (a.reference,a.source_key,a.is_balanced) for a in tbr.adjustments])
    import engagements as E
    _,_,cd,ad=E._ppe_plain_data(EID)
    mv,pol=E._ppe_movement_and_policy(cd,ad,date(2025,12,31))
    tt=mv["total"]; print("movement open",tt["opening_nbv"],"reval",tt["revaluation"],"close",tt["closing_nbv"])
    st=fin.build_all_statements(tbr.lines,tbr.adjustments,reporting_framework="ifrs_for_smes",ppe_movement=mv,ppe_policy_text=pol,whole_numbers=True)
    print("PPE",st["totals"]["ppe"]["current"],"reserves",st["totals"]["other_reserves"]["current"],"TA",st["sfp"]["total_assets"]["current"],"TEL",st["sfp"]["total_equity_and_liabilities"]["current"])
    n=next(n for n in st["notes"] if n["title"]=="Property, plant and equipment"); print("ties",n["reconciliation"]["ties_out"],n["reconciliation"])

assert n["reconciliation"]["ties_out"], "note must tie out"
assert abs(st["totals"]["ppe"]["current"]-2358200)<0.5
assert abs(st["sfp"]["total_assets"]["current"]-st["sfp"]["total_equity_and_liabilities"]["current"])<0.5
assert [a.source_key for a in tbr.adjustments]==["REVAL"]
print("button shown"); print("PASS one-click post applies and links to statements and note")
