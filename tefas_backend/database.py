import os
import datetime
from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session, select

# DB, tr-data-dashboard/data/tefas.db olarak saklanır
_HERE = os.path.dirname(os.path.abspath(__file__))
_DB_PATH = os.path.join(_HERE, "..", "data", "tefas.db")
DATABASE_URL = f"sqlite:///{os.path.normpath(_DB_PATH)}"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},
)


class FundDaily(SQLModel, table=True):
    __tablename__ = "fund_daily"

    id: Optional[int] = Field(default=None, primary_key=True)
    trade_date: datetime.date = Field(index=True)
    code: str = Field(index=True)
    fname: Optional[str] = Field(default=None)
    fund_type: Optional[str] = Field(default=None)
    price: Optional[float] = Field(default=None)
    aum: Optional[float] = Field(default=None)
    shares: Optional[float] = Field(default=None)
    investors: Optional[int] = Field(default=None)


class FundFlow(SQLModel, table=True):
    __tablename__ = "fund_flow"

    id: Optional[int] = Field(default=None, primary_key=True)
    trade_date: datetime.date = Field(index=True)
    code: str = Field(index=True)
    fname: Optional[str] = Field(default=None)
    fund_type: Optional[str] = Field(default=None)
    net_flow: Optional[float] = Field(default=None)
    flow_pct: Optional[float] = Field(default=None)
    aum_change: Optional[float] = Field(default=None)
    aum: Optional[float] = Field(default=None)


class FundComposition(SQLModel, table=True):
    """Günlük portföy dağılımı (dagilimSiraliGetirDosya endpoint'inden)."""
    __tablename__ = "fund_composition"

    id: Optional[int] = Field(default=None, primary_key=True)
    trade_date: datetime.date = Field(index=True)
    code: str = Field(index=True)
    fname: Optional[str] = Field(default=None)

    hs: Optional[float] = Field(default=None)
    yhs: Optional[float] = Field(default=None)
    dt: Optional[float] = Field(default=None)
    dot: Optional[float] = Field(default=None)
    hb: Optional[float] = Field(default=None)
    tr: Optional[float] = Field(default=None)
    fb: Optional[float] = Field(default=None)
    eut: Optional[float] = Field(default=None)
    kks: Optional[float] = Field(default=None)
    kksd: Optional[float] = Field(default=None)
    kkstl: Optional[float] = Field(default=None)
    kksyd: Optional[float] = Field(default=None)
    khau: Optional[float] = Field(default=None)
    khd: Optional[float] = Field(default=None)
    khtl: Optional[float] = Field(default=None)
    kh: Optional[float] = Field(default=None)
    vdm: Optional[float] = Field(default=None)
    vmd: Optional[float] = Field(default=None)
    vmtl: Optional[float] = Field(default=None)
    vmau: Optional[float] = Field(default=None)
    vm: Optional[float] = Field(default=None)
    vint: Optional[float] = Field(default=None)
    byf: Optional[float] = Field(default=None)
    kmbyf: Optional[float] = Field(default=None)
    ybyf: Optional[float] = Field(default=None)
    bb: Optional[float] = Field(default=None)
    yyf: Optional[float] = Field(default=None)
    ymk: Optional[float] = Field(default=None)
    gsykb: Optional[float] = Field(default=None)
    gsyy: Optional[float] = Field(default=None)
    gykb: Optional[float] = Field(default=None)
    gyy: Optional[float] = Field(default=None)
    r: Optional[float] = Field(default=None)
    t: Optional[float] = Field(default=None)
    ost: Optional[float] = Field(default=None)
    osks: Optional[float] = Field(default=None)
    osdb: Optional[float] = Field(default=None)
    oksyd: Optional[float] = Field(default=None)
    fkb: Optional[float] = Field(default=None)
    kba: Optional[float] = Field(default=None)
    kmkba: Optional[float] = Field(default=None)
    kmkks: Optional[float] = Field(default=None)
    kibd: Optional[float] = Field(default=None)
    yba: Optional[float] = Field(default=None)
    ybkb: Optional[float] = Field(default=None)
    ybosb: Optional[float] = Field(default=None)
    gas: Optional[float] = Field(default=None)
    tpp: Optional[float] = Field(default=None)
    bpp: Optional[float] = Field(default=None)
    btaa: Optional[float] = Field(default=None)
    btas: Optional[float] = Field(default=None)
    d: Optional[float] = Field(default=None)
    db: Optional[float] = Field(default=None)
    km: Optional[float] = Field(default=None)
    bilFiyat: Optional[float] = Field(default=None)


class FundMeta(SQLModel, table=True):
    """Her fonun sabit metadata'sı — kategori, fon tipi, isim."""
    __tablename__ = "fund_meta"

    code: str = Field(primary_key=True)
    fname: Optional[str] = Field(default=None)
    fund_type: Optional[str] = Field(default=None, index=True)
    category: Optional[str] = Field(default=None, index=True)
    updated_at: Optional[datetime.date] = Field(default=None)


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


Date = datetime.date
