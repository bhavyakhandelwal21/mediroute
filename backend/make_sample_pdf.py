"""Makes samples/sample_hospital_report.pdf (made-up data) so you can test the PDF upload. Needs: pip install reportlab"""
import datetime, random, pathlib
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, LongTable, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet

random.seed(4)
FAC = [("Sunrise Hospital", "Indirapuram", 28.6430, 77.3690, 4), ("Green Valley Clinic", "Vaishali", 28.6460, 77.3400, 6),
       ("Lotus Health Centre", "Raj Nagar", 28.6700, 77.4380, 5)]
MED = [("Paracetamol 500mg", 22), ("ORS sachets", 14), ("Amoxicillin 250mg", 12)]
sty = getSampleStyleSheet()
grid = TableStyle([("GRID", (0, 0), (-1, -1), .5, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("FONTSIZE", (0, 0), (-1, -1), 8)])
stock = [["Facility", "Locality", "Latitude", "Longitude", "Medicine", "Stock", "Expiry (days)", "Lead days", "Incoming"]]
usage = [["Facility", "Medicine", "Date", "Used"]]
end = datetime.date(2026, 10, 17)
for i, (f, loc, la, lo, lead) in enumerate(FAC):
    for j, (m, rate) in enumerate(MED):
        s = [60, 380, 900][(i + j) % 3] * rate // 20
        stock.append([f, loc, la, lo, m, s, "" if (i, j) == (1, 1) else random.randint(25, 110), lead, 0])   # one blank expiry on purpose
        for d in range(60):
            if random.random() < .05: continue
            usage.append([f, m, str(end - datetime.timedelta(days=59 - d)), max(0, int(random.gauss(rate, rate * .2)))])
out = pathlib.Path(__file__).parent / "samples"; out.mkdir(exist_ok=True)
t1 = LongTable(stock, repeatRows=1); t1.setStyle(grid)
t2 = LongTable(usage, repeatRows=1); t2.setStyle(grid)
SimpleDocTemplate(str(out / "sample_hospital_report.pdf"), pagesize=A4).build(
    [Paragraph("Stock register (made-up data)", sty["Heading2"]), t1, PageBreak(), Paragraph("Daily usage log (made-up data)", sty["Heading2"]), t2])
print("Created", out / "sample_hospital_report.pdf")
