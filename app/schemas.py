from pydantic import BaseModel, field_validator
from typing import List, Optional

class SerpRequest(BaseModel):
    keywords: List[str]
    country_code: str = "it"
    site_filter: str = "it.linkedin.com"
    max_pages: int = 10

    @field_validator("max_pages")
    @classmethod
    def _max_pages(cls, v):
        return max(1, min(50, v))

class PeopleRow(BaseModel):
    Nome: str
    Cognome: str
    Title: Optional[str]
    Snippet: Optional[str]
    Location: Optional[str]
    Followers: Optional[int]
    LinkedIn: str

class SerpResponse(BaseModel):
    query: str
    count_pages: int
    people: List[PeopleRow]
    raw_items_count: int
