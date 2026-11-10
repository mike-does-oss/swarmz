"""
本体生成服务
接口1：分析文本内容，生成适合社会模拟的实体和关系类型定义
"""

import json
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient


# 本体生成的系统提示词
ONTOLOGY_SYSTEM_PROMPT = """You are an expert knowledge-graph ontology designer. Your task is to analyse the given text and simulation requirement and design entity types and relationship types suited to a **social-media public-discussion simulation**.

**Important: output valid JSON only. Output nothing else.**

## Background

We are building a **social-media discussion simulation**. In this system:
- every entity is an "account" or actor that can speak, interact and spread information on social media
- entities influence, repost, comment on and respond to one another
- we simulate how each party reacts to an event and how information spreads

Therefore, **entities must be real actors that can speak and interact on social media**:

**Allowed**:
- specific individuals (public figures, people involved, opinion leaders, experts, ordinary people)
- companies and businesses (including their official accounts)
- organisations (universities, associations, NGOs, unions, teams, leagues)
- government departments and regulators
- media organisations (newspapers, broadcasters, independent media, websites)
- the social-media platforms themselves
- representatives of specific groups (alumni associations, fan groups, advocacy groups)

**Not allowed**:
- abstract concepts (e.g. "public opinion", "sentiment", "trend")
- topics (e.g. "academic integrity", "education reform")
- positions or attitudes (e.g. "supporters", "opponents")

## Output format

Return JSON with this structure:

```json
{
    "entity_types": [
        {
            "name": "EntityTypeName (English, PascalCase)",
            "description": "short description (English, at most 100 characters)",
            "attributes": [
                {
                    "name": "attribute_name (English, snake_case)",
                    "type": "text",
                    "description": "attribute description"
                }
            ],
            "examples": ["example entity 1", "example entity 2"]
        }
    ],
    "edge_types": [
        {
            "name": "RELATIONSHIP_NAME (English, UPPER_SNAKE_CASE)",
            "description": "short description (English, at most 100 characters)",
            "source_targets": [
                {"source": "SourceEntityType", "target": "TargetEntityType"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "brief analysis of the text (English)"
}
```

## Design guidelines (very important)

### 1. Entity types - follow strictly

**Count: exactly 10 entity types**

**Hierarchy: include both specific types and fallback types**

Your 10 entity types must contain:

A. **Fallback types (required, the last 2 in the list)**:
   - `Person`: fallback for any individual who does not fit a more specific person type.
   - `Organization`: fallback for any organisation that does not fit a more specific organisation type.

B. **Specific types (8, designed from the text)**:
   - design more specific types for the main roles that appear in the text
   - for example, an academic event might use `Student`, `Professor`, `University`
   - for example, a business event might use `Company`, `CEO`, `Employee`
   - for example, a sports event might use `Team`, `Player`, `Coach`, `League`, `Sportsbook`

**Why fallback types are needed**:
- the text will mention all sorts of people, such as "a schoolteacher", "a bystander", "a commenter"
- if no specific type matches, they belong in `Person`
- likewise, small organisations and ad-hoc groups belong in `Organization`

**Principles for specific types**:
- identify the roles that appear frequently or matter most in the text
- give each specific type a clear boundary; avoid overlap
- the description must make clear how the type differs from the fallback

### 2. Relationship types

- count: 6-10
- relationships should reflect real connections seen in social-media interaction
- make sure source_targets cover the entity types you defined

### 3. Attributes

- 1-3 key attributes per entity type
- **note**: attribute names must not be `name`, `uuid`, `group_id`, `created_at` or `summary` (reserved by the system)
- prefer `full_name`, `title`, `role`, `position`, `location`, `description`, etc.

## Entity type reference

**Individuals (specific)**:
- Student, Professor, Journalist, Celebrity, Executive, Official, Lawyer, Doctor, Player, Coach, Analyst

**Individuals (fallback)**:
- Person: any individual not covered by a specific type

**Organisations (specific)**:
- University, Company, GovernmentAgency, MediaOutlet, Hospital, School, NGO, Team, League, Sportsbook, Exchange

**Organisations (fallback)**:
- Organization: any organisation not covered by a specific type

## Relationship type reference

- WORKS_FOR, STUDIES_AT, AFFILIATED_WITH, REPRESENTS, REGULATES, REPORTS_ON, COMMENTS_ON, RESPONDS_TO, SUPPORTS, OPPOSES, COLLABORATES_WITH, COMPETES_WITH, PLAYS_FOR, COACHES

Write every description, example and the analysis_summary in English.
"""


class OntologyGenerator:
    """
    本体生成器
    分析文本内容，生成实体和关系类型定义
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        生成本体定义
        
        Args:
            document_texts: 文档文本列表
            simulation_requirement: 模拟需求描述
            additional_context: 额外上下文
            
        Returns:
            本体定义（entity_types, edge_types等）
        """
        # 构建用户消息
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        messages = [
            {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        # 调用LLM
        # 8192: ten typed entities plus the summary overran 4096 and came back as cut-off JSON
        result = self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            max_tokens=8192
        )
        
        # 验证和后处理
        result = self._validate_and_process(result)
        
        return result
    
    # 传给 LLM 的文本最大长度（5万字）
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """构建用户消息"""
        
        # 合并文本
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)
        
        # 如果文本超过5万字，截断（仅影响传给LLM的内容，不影响图谱构建）
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(original text is {original_length} characters; the first {self.MAX_TEXT_LENGTH_FOR_LLM} are shown for ontology analysis)..."
        
        message = f"""## Simulation requirement

{simulation_requirement}

## Document content

{combined_text}
"""
        
        if additional_context:
            message += f"""
## Additional notes

{additional_context}
"""
        
        message += """
Based on the material above, design entity types and relationship types suited to a social-media discussion simulation.

**Rules**:
1. output exactly 10 entity types
2. the last 2 must be the fallback types Person and Organization
3. the first 8 are specific types designed from the text
4. every entity type must be a real actor that can speak; no abstract concepts
5. attribute names must not use reserved words such as name, uuid or group_id; use full_name, org_name, etc.
6. write everything in English
"""
        
        return message
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """验证和后处理结果"""
        
        # 确保必要字段存在
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        # 验证实体类型
        for entity in result["entity_types"]:
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # 确保description不超过100字符
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # 验证关系类型
        for edge in result["edge_types"]:
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."
        
        # Zep API 限制：最多 10 个自定义实体类型，最多 10 个自定义边类型
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10
        
        # 兜底类型定义
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # 检查是否已有兜底类型
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # 需要添加的兜底类型
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # 如果添加后会超过 10 个，需要移除一些现有类型
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # 计算需要移除多少个
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # 从末尾移除（保留前面更重要的具体类型）
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # 添加兜底类型
            result["entity_types"].extend(fallbacks_to_add)
        
        # 最终确保不超过限制（防御性编程）
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]
        
        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        将本体定义转换为Python代码（类似ontology.py）
        
        Args:
            ontology: 本体定义
            
        Returns:
            Python代码字符串
        """
        code_lines = [
            '"""',
            '自定义实体类型定义',
            '由MiroFish自动生成，用于社会舆论模拟',
            '"""',
            '',
            'from pydantic import Field',
            'from app.memory_compat import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== 实体类型定义 ==============',
            '',
        ]
        
        # 生成实体类型
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== 关系类型定义 ==============')
        code_lines.append('')
        
        # 生成关系类型
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # 转换为PascalCase类名
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # 生成类型字典
        code_lines.append('# ============== 类型配置 ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # 生成边的source_targets映射
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)

