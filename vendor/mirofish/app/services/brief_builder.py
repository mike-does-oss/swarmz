"""
Brief Builder Service
Multi-agent pipeline for research brief generation using Brave Search and Perplexity APIs
"""

import os
import json
import uuid
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

import requests

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger('mirofish.services.brief_builder')

# API Configuration
BRAVE_API_KEY = os.environ.get('BRAVE_API_KEY', '')
PERPLEXITY_API_KEY = os.environ.get('PERPLEXITY_API_KEY', '')

# Depth mapping: number of research dimensions
DEPTH_MAPPING = {
    'quick': 3,
    'standard': 6,
    'deep': 10
}

# Brief storage directory
BRIEFS_DIR = os.path.join(os.path.dirname(__file__), '../../data/briefs')


@dataclass
class BriefSection:
    """A section within the research brief"""
    title: str
    content: str
    sources: List[str] = field(default_factory=list)


@dataclass
class Brief:
    """Research brief data structure"""
    brief_id: str
    topic: str
    depth: str
    status: str  # 'pending', 'building', 'completed', 'error'
    created_at: str
    updated_at: str
    sections: List[BriefSection] = field(default_factory=list)
    summary: str = ''
    raw_search_results: List[Dict] = field(default_factory=list)
    perplexity_insights: List[str] = field(default_factory=list)
    error_message: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'brief_id': self.brief_id,
            'topic': self.topic,
            'depth': self.depth,
            'status': self.status,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'sections': [asdict(s) for s in self.sections],
            'summary': self.summary,
            'raw_search_results': self.raw_search_results,
            'perplexity_insights': self.perplexity_insights,
            'error_message': self.error_message
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Brief':
        sections = [BriefSection(**s) for s in data.get('sections', [])]
        return cls(
            brief_id=data['brief_id'],
            topic=data['topic'],
            depth=data['depth'],
            status=data['status'],
            created_at=data['created_at'],
            updated_at=data['updated_at'],
            sections=sections,
            summary=data.get('summary', ''),
            raw_search_results=data.get('raw_search_results', []),
            perplexity_insights=data.get('perplexity_insights', []),
            error_message=data.get('error_message', '')
        )


class BraveSearchAgent:
    """Agent for web search using Brave Search API"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or BRAVE_API_KEY
        self.base_url = 'https://api.search.brave.com/res/v1/web/search'

    def search(self, query: str, count: int = 5) -> List[Dict[str, Any]]:
        """
        Perform web search using Brave Search API

        Args:
            query: Search query
            count: Number of results to return

        Returns:
            List of search results with title, url, and description
        """
        try:
            headers = {
                'Accept': 'application/json',
                'X-Subscription-Token': self.api_key
            }
            params = {
                'q': query,
                'count': count
            }

            response = requests.get(
                self.base_url,
                headers=headers,
                params=params,
                timeout=30
            )
            response.raise_for_status()

            data = response.json()
            results = []

            # Extract web results
            web_results = data.get('web', {}).get('results', [])
            for result in web_results[:count]:
                results.append({
                    'title': result.get('title', ''),
                    'url': result.get('url', ''),
                    'description': result.get('description', '')
                })

            logger.info(f"Brave Search returned {len(results)} results for: {query[:50]}...")
            return results

        except requests.RequestException as e:
            logger.error(f"Brave Search error: {e}")
            return []


class PerplexityResearchAgent:
    """Agent for deep research using Perplexity API"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or PERPLEXITY_API_KEY
        self.base_url = 'https://api.perplexity.ai/chat/completions'

    def research(self, query: str, context: str = '') -> str:
        """
        Perform deep research using Perplexity's sonar model

        Args:
            query: Research question
            context: Additional context for the research

        Returns:
            Research insights as text
        """
        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }

            messages = []
            if context:
                messages.append({
                    'role': 'system',
                    'content': f'Use this context for your research: {context}'
                })
            messages.append({
                'role': 'user',
                'content': query
            })

            payload = {
                'model': 'sonar',
                'messages': messages
            }

            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()

            data = response.json()
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')

            logger.info(f"Perplexity research completed for: {query[:50]}...")
            return content

        except requests.RequestException as e:
            logger.error(f"Perplexity API error: {e}")
            return ''


class BriefBuilder:
    """
    Multi-agent pipeline for building research briefs

    Pipeline stages:
    1. Dimension expansion: Use LLM to expand topic into research dimensions
    2. Web search: Use Brave Search to gather relevant sources
    3. Deep research: Use Perplexity to analyze and synthesize
    4. Brief synthesis: Use GPT-5 (via LLMClient) to create final brief
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()
        self.brave_agent = BraveSearchAgent()
        self.perplexity_agent = PerplexityResearchAgent()

        # Ensure briefs directory exists
        os.makedirs(BRIEFS_DIR, exist_ok=True)

    def build_async(self, topic: str, depth: str = 'standard') -> str:
        """
        Start async brief building process

        Args:
            topic: Research topic
            depth: Research depth ('quick', 'standard', 'deep')

        Returns:
            brief_id for tracking
        """
        brief_id = f"brief_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat() + 'Z'

        brief = Brief(
            brief_id=brief_id,
            topic=topic,
            depth=depth,
            status='pending',
            created_at=now,
            updated_at=now
        )

        # Save initial brief
        self._save_brief(brief)

        # Start building in background thread
        thread = threading.Thread(
            target=self._build_worker,
            args=(brief_id,)
        )
        thread.daemon = True
        thread.start()

        return brief_id

    def _build_worker(self, brief_id: str):
        """Background worker for brief building"""
        try:
            brief = self.get_brief(brief_id)
            if not brief:
                logger.error(f"Brief not found: {brief_id}")
                return

            brief.status = 'building'
            brief.updated_at = datetime.utcnow().isoformat() + 'Z'
            self._save_brief(brief)

            # Stage 1: Expand topic into research dimensions
            num_dimensions = DEPTH_MAPPING.get(brief.depth, 6)
            dimensions = self._expand_dimensions(brief.topic, num_dimensions)
            logger.info(f"Expanded {brief.topic} into {len(dimensions)} dimensions")

            # Stage 2: Search for each dimension
            all_search_results = []
            for dimension in dimensions:
                results = self.brave_agent.search(f"{brief.topic} {dimension}", count=5)
                all_search_results.extend(results)
            brief.raw_search_results = all_search_results
            self._save_brief(brief)

            # Stage 3: Deep research using Perplexity
            perplexity_insights = []
            for dimension in dimensions[:3]:  # Limit to avoid rate limits
                context = f"Topic: {brief.topic}. Dimension: {dimension}"
                insight = self.perplexity_agent.research(
                    f"Provide key insights about {dimension} in the context of {brief.topic}",
                    context=context
                )
                if insight:
                    perplexity_insights.append(insight)
            brief.perplexity_insights = perplexity_insights
            self._save_brief(brief)

            # Stage 4: Synthesize final brief
            sections, summary = self._synthesize_brief(
                topic=brief.topic,
                dimensions=dimensions,
                search_results=all_search_results,
                insights=perplexity_insights
            )

            brief.sections = sections
            brief.summary = summary
            brief.status = 'completed'
            brief.updated_at = datetime.utcnow().isoformat() + 'Z'
            self._save_brief(brief)

            logger.info(f"Brief completed: {brief_id}")

        except Exception as e:
            logger.error(f"Brief building failed: {e}")
            brief = self.get_brief(brief_id)
            if brief:
                brief.status = 'error'
                brief.error_message = str(e)
                brief.updated_at = datetime.utcnow().isoformat() + 'Z'
                self._save_brief(brief)

    def _expand_dimensions(self, topic: str, num_dimensions: int) -> List[str]:
        """Use LLM to expand topic into research dimensions"""
        prompt = f"""Given the topic "{topic}", generate exactly {num_dimensions} distinct research dimensions or angles to explore.

Return as a JSON array of strings, each being a concise dimension (2-5 words).

Example for topic "Electric Vehicles":
["Battery Technology", "Charging Infrastructure", "Environmental Impact", "Market Adoption", "Government Policies", "Manufacturing Challenges"]

Return ONLY the JSON array, no other text."""

        try:
            response = self.llm.chat_json([
                {'role': 'user', 'content': prompt}
            ])

            # Handle both direct array and wrapped response
            if isinstance(response, list):
                return response[:num_dimensions]
            elif isinstance(response, dict) and 'dimensions' in response:
                return response['dimensions'][:num_dimensions]
            else:
                # Fallback: extract array from response
                return list(response.values())[0][:num_dimensions] if response else []

        except Exception as e:
            logger.error(f"Dimension expansion failed: {e}")
            # Fallback dimensions
            return [f"Aspect {i+1}" for i in range(num_dimensions)]

    def _synthesize_brief(
        self,
        topic: str,
        dimensions: List[str],
        search_results: List[Dict],
        insights: List[str]
    ) -> tuple[List[BriefSection], str]:
        """Use LLM to synthesize final brief from research data"""

        # Prepare search context
        search_context = "\n".join([
            f"- {r['title']}: {r['description'][:200]}"
            for r in search_results[:15]
        ])

        # Prepare insights context
        insights_context = "\n\n".join(insights[:5])

        prompt = f"""Create a comprehensive research brief about "{topic}".

Research Dimensions: {', '.join(dimensions)}

Web Search Findings:
{search_context}

Deep Research Insights:
{insights_context}

Create a structured brief with:
1. Executive Summary (2-3 sentences)
2. One section per research dimension with title and detailed content
3. Include source URLs where relevant

Return as JSON:
{{
    "summary": "Executive summary here",
    "sections": [
        {{
            "title": "Section Title",
            "content": "Detailed section content...",
            "sources": ["url1", "url2"]
        }}
    ]
}}"""

        try:
            response = self.llm.chat_json([
                {'role': 'system', 'content': 'You are a research analyst creating comprehensive briefs.'},
                {'role': 'user', 'content': prompt}
            ], max_tokens=8000)

            summary = response.get('summary', '')
            sections = [
                BriefSection(
                    title=s.get('title', ''),
                    content=s.get('content', ''),
                    sources=s.get('sources', [])
                )
                for s in response.get('sections', [])
            ]

            return sections, summary

        except Exception as e:
            logger.error(f"Brief synthesis failed: {e}")
            return [], f"Research brief for: {topic}"

    def get_brief(self, brief_id: str) -> Optional[Brief]:
        """Get brief by ID"""
        file_path = os.path.join(BRIEFS_DIR, f"{brief_id}.json")
        if not os.path.exists(file_path):
            return None

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return Brief.from_dict(data)

    def list_briefs(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """List all briefs with pagination"""
        briefs = []

        if not os.path.exists(BRIEFS_DIR):
            return []

        files = sorted(
            [f for f in os.listdir(BRIEFS_DIR) if f.endswith('.json')],
            reverse=True
        )

        for filename in files[offset:offset + limit]:
            file_path = os.path.join(BRIEFS_DIR, filename)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Return summary info only
            briefs.append({
                'brief_id': data['brief_id'],
                'topic': data['topic'],
                'depth': data['depth'],
                'status': data['status'],
                'created_at': data['created_at'],
                'updated_at': data['updated_at']
            })

        return briefs

    def _save_brief(self, brief: Brief):
        """Save brief to file"""
        file_path = os.path.join(BRIEFS_DIR, f"{brief.brief_id}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(brief.to_dict(), f, ensure_ascii=False, indent=2)

    def convert_to_seed_document(self, brief_id: str) -> Optional[str]:
        """
        Convert a brief into a seed document format for MiroFish simulation

        Args:
            brief_id: Brief ID to convert

        Returns:
            Markdown-formatted seed document text
        """
        brief = self.get_brief(brief_id)
        if not brief or brief.status != 'completed':
            return None

        # Build markdown document
        lines = [
            f"# Research Brief: {brief.topic}",
            "",
            "## Executive Summary",
            brief.summary,
            ""
        ]

        for section in brief.sections:
            lines.append(f"## {section.title}")
            lines.append(section.content)
            if section.sources:
                lines.append("")
                lines.append("**Sources:**")
                for source in section.sources:
                    lines.append(f"- {source}")
            lines.append("")

        return "\n".join(lines)
