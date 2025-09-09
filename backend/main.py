
# --- Imports ---
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from geoalchemy2 import Geography
from geoalchemy2.functions import ST_DWithin
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

# --- Database Setup ---
DATABASE_URL = "postgresql+psycopg2://postgres:yN5viuhmel@localhost:5432/geodb"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    point = Column(Geography(geometry_type='POINT', srid=4326))
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# --- FastAPI App Setup ---
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- DB Initialization ---
def init_db():
    db = SessionLocal()
    db.query(Location).delete()
    db.commit()
    init_lat, init_lng = 13.7563, 100.5018
    point_wkt = f'POINT({init_lng} {init_lat})'
    loc = Location(username='init', point=point_wkt)
    db.add(loc)
    db.commit()
    db.close()

init_db()

# --- Schemas ---
class LocationCreate(BaseModel):
    username: str
    latitude: float
    longitude: float

# --- Endpoints ---
@app.post("/location")
def add_location(data: LocationCreate, db=Depends(get_db)):
    point_wkt = f'POINT({data.longitude} {data.latitude})'
    loc = Location(username=data.username, point=point_wkt)
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return {
        "id": loc.id,
        "username": data.username,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "timestamp": loc.timestamp
    }

@app.get("/nearby")
def get_nearby_users(latitude: float, longitude: float, radius: float, db=Depends(get_db)):
    point_wkt = f'POINT({longitude} {latitude})'
    query = db.query(Location).filter(
        Location.point != None,
        ST_DWithin(Location.point, point_wkt, radius)
    )
    results = query.all()
    return [
        {
            "id": loc.id,
            "username": loc.username,
            "timestamp": loc.timestamp
        }
        for loc in results
    ]

@app.get("/points")
def get_all_points(db=Depends(get_db)):
    # Query all points with lat/lng extracted using ST_X/ST_Y
    sql = text("""
        SELECT id, username, timestamp,
               ST_X(point::geometry) AS lng,
               ST_Y(point::geometry) AS lat
        FROM locations
        ORDER BY id
    """)
    rows = db.execute(sql).fetchall()
    if len(rows) < 2:
        return [
            {
                "id": row.id,
                "username": row.username,
                "latitude": row.lat,
                "longitude": row.lng,
                "timestamp": row.timestamp,
                "distance": None,
                "accuracy": 100.0,
                "is_first": True,
                "is_last": True
            }
            for row in rows
        ]
    first = rows[0]
    last = rows[-1]
    line_wkt = f'LINESTRING({first.lng} {first.lat}, {last.lng} {last.lat})'
    result = []
    distances = []
    for idx, row in enumerate(rows):
        dist = None
        if idx != 0 and idx != len(rows) - 1:
            sql_dist = text("""
                SELECT ST_Distance(
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    ST_GeomFromText(:line, 4326)::geography
                )
            """)
            res = db.execute(sql_dist, {"lng": row.lng, "lat": row.lat, "line": line_wkt}).fetchone()
            dist = res[0] if res else None
        else:
            dist = 0.0
        distances.append(dist)
    # Only non-endpoint pins are counted for accuracy
    non_end_indices = [i for i in range(len(rows)) if i != 0 and i != len(rows) - 1]
    non_end_distances = [distances[i] for i in non_end_indices]
    total_distance = sum(non_end_distances)
    if len(non_end_indices) == 0:
        accuracies = [None] * len(rows)
    elif total_distance == 0:
        # All non-end pins on line
        acc_val = 100.0 / len(non_end_indices)
        accuracies = [acc_val if i in non_end_indices else None for i in range(len(rows))]
    else:
        inverted = [1.0 / (d + 1e-6) for d in non_end_distances]
        total_inverted = sum(inverted)
        accs = [v / total_inverted * 100.0 for v in inverted]
        accuracies = [accs[non_end_indices.index(i)] if i in non_end_indices else None for i in range(len(rows))]
    for idx, row in enumerate(rows):
        dist = distances[idx]
        acc = accuracies[idx]
        result.append({
            "id": row.id,
            "username": row.username,
            "latitude": row.lat,
            "longitude": row.lng,
            "timestamp": row.timestamp,
            "distance": dist,
            "accuracy": acc,
            "is_first": idx == 0,
            "is_last": idx == len(rows) - 1
        })
    return result
