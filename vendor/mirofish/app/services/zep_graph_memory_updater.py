"""
Zep图谱记忆更新服务
将模拟中的Agent活动动态更新到Zep图谱中
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

# swarmz: Zep Cloud replaced by the local memory shim (app/memory_compat.py)
from .. import memory_compat
from ..memory_compat import Zep

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.zep_graph_memory_updater')


@dataclass
class AgentActivity:
    """Agent活动记录"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str
    
    def to_episode_text(self) -> str:
        """
        将活动转换为可以发送给Zep的文本描述
        
        采用自然语言描述格式，让Zep能够从中提取实体和关系
        不添加模拟相关的前缀，避免误导图谱更新
        """
        # 根据不同的动作类型生成不同的描述
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # 直接返回 "agent名称: 活动描述" 格式，不添加模拟前缀
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f'posted: "{content}"'
        return "posted"

    def _describe_like_post(self) -> str:
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if post_content and post_author:
            return f'liked {post_author}\'s post: "{post_content}"'
        elif post_content:
            return f'liked a post: "{post_content}"'
        elif post_author:
            return f"liked a post by {post_author}"
        return "liked a post"

    def _describe_dislike_post(self) -> str:
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if post_content and post_author:
            return f'disliked {post_author}\'s post: "{post_content}"'
        elif post_content:
            return f'disliked a post: "{post_content}"'
        elif post_author:
            return f"disliked a post by {post_author}"
        return "disliked a post"

    def _describe_repost(self) -> str:
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        if original_content and original_author:
            return f'reposted {original_author}\'s post: "{original_content}"'
        elif original_content:
            return f'reposted a post: "{original_content}"'
        elif original_author:
            return f"reposted a post by {original_author}"
        return "reposted a post"

    def _describe_quote_post(self) -> str:
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        if original_content and original_author:
            base = f'quoted {original_author}\'s post "{original_content}"'
        elif original_content:
            base = f'quoted a post "{original_content}"'
        elif original_author:
            base = f"quoted a post by {original_author}"
        else:
            base = "quoted a post"
        if quote_content:
            base += f', adding: "{quote_content}"'
        return base

    def _describe_follow(self) -> str:
        target_user_name = self.action_args.get("target_user_name", "")
        if target_user_name:
            return f"followed {target_user_name}"
        return "followed a user"

    def _describe_create_comment(self) -> str:
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if content:
            if post_content and post_author:
                return f'replied to {post_author}\'s post "{post_content}": "{content}"'
            elif post_content:
                return f'replied to the post "{post_content}": "{content}"'
            elif post_author:
                return f'replied to {post_author}\'s post: "{content}"'
            return f'commented: "{content}"'
        return "left a comment"

    def _describe_like_comment(self) -> str:
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        if comment_content and comment_author:
            return f'liked {comment_author}\'s comment: "{comment_content}"'
        elif comment_content:
            return f'liked a comment: "{comment_content}"'
        elif comment_author:
            return f"liked a comment by {comment_author}"
        return "liked a comment"

    def _describe_dislike_comment(self) -> str:
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        if comment_content and comment_author:
            return f'disliked {comment_author}\'s comment: "{comment_content}"'
        elif comment_content:
            return f'disliked a comment: "{comment_content}"'
        elif comment_author:
            return f"disliked a comment by {comment_author}"
        return "disliked a comment"

    def _describe_search(self) -> str:
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f'searched for "{query}"' if query else "searched posts"

    def _describe_search_user(self) -> str:
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f'searched for user "{query}"' if query else "searched users"

    def _describe_mute(self) -> str:
        target_user_name = self.action_args.get("target_user_name", "")
        if target_user_name:
            return f"muted {target_user_name}"
        return "muted a user"

    def _describe_generic(self) -> str:
        return f"performed {self.action_type}"


@dataclass
class _FlushMarker:
    round_num: int
    platform: Optional[str] = None


class ZepGraphMemoryUpdater:
    """
    图谱记忆更新器 (swarmz: local Graphiti memory via app/memory_compat.py)

    监控模拟的actions日志文件，将新的agent活动更新到图谱中。

    swarmz changes vs upstream (which sent every BATCH_SIZE actions per platform as
    one mixed episode with no reference time):
      * activities are buffered per (platform, round, agent);
      * a round is flushed when a later round's activity arrives, on an explicit
        ``flush_round`` (round_end event), or on stop;
      * a flush writes ONE episode per agent (that agent's actions in the round,
        joined by newlines) into the agent's own group ``agent_<id>``, all agents of
        the round in a single ``add_batch`` call (the backend groups by agent);
      * each episode's reference_time is the simulated time for its round, clamped
        to never be after the question's freeze time
        (``memory_compat.reference_time_for_round``).
    """

    # 平台名称映射（用于控制台显示）
    PLATFORM_DISPLAY_NAMES = {
        'twitter': 'world1',
        'reddit': 'world2',
    }

    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # 秒

    def __init__(self, graph_id: str, api_key: Optional[str] = None):
        """
        Args:
            graph_id: 图谱ID
            api_key: legacy, ignored
        """
        self.graph_id = graph_id
        self.client = Zep(api_key=api_key)

        # 活动队列
        self._activity_queue: Queue = Queue()

        # platform -> round -> agent_id -> [AgentActivity]
        self._round_buffers: Dict[str, Dict[int, Dict[int, List[AgentActivity]]]] = {}
        self._buffer_lock = threading.Lock()
        self._send_lock = threading.Lock()

        # 控制标志
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        # 统计
        self._total_activities = 0  # 实际添加到队列的活动数
        self._total_sent = 0        # 成功发送的批次数（每轮一次）
        self._total_items_sent = 0  # 成功发送的活动条数
        self._episodes_sent = 0     # 写入的episode数（每轮每agent一个）
        self._failed_count = 0      # 发送失败的批次数
        self._skipped_count = 0     # 被过滤跳过的活动数（DO_NOTHING）

        logger.info(f"ZepGraphMemoryUpdater 初始化完成: graph_id={graph_id} (per-agent per-round batches)")

    def _get_platform_display_name(self, platform: str) -> str:
        """获取平台的显示名称"""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)

    def start(self):
        """启动后台工作线程"""
        if self._running:
            return

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater 已启动: graph_id={self.graph_id}")

    def stop(self):
        """停止后台工作线程，并写入所有剩余活动"""
        self._running = False

        # The worker drains the queue (graph ingestion is LLM-bound and can take a
        # while); wait for it so callers can read memory right after stop().
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=float(os.environ.get('SWARMZ_MEMORY_FLUSH_TIMEOUT', '900')))

        self._flush_remaining()

        logger.info(f"ZepGraphMemoryUpdater 已停止: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"episodes_sent={self._episodes_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")

    def add_activity(self, activity: AgentActivity):
        """添加一个agent活动到队列（DO_NOTHING 跳过）"""
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return

        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"添加活动到队列: {activity.agent_name} - {activity.action_type}")

    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
        从字典数据添加活动

        Args:
            data: 从actions.jsonl解析的字典数据
            platform: 平台名称 (twitter/reddit)
        """
        # 跳过事件类型的条目
        if "event_type" in data:
            return

        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )

        self.add_activity(activity)

    def _buffer(self, activity: AgentActivity) -> List[tuple]:
        """Buffer one activity; return (platform, round) keys of rounds that are now complete."""
        platform = activity.platform.lower()
        rnd = int(activity.round_num or 0)
        with self._buffer_lock:
            rounds = self._round_buffers.setdefault(platform, {})
            rounds.setdefault(rnd, {}).setdefault(activity.agent_id, []).append(activity)
            return [(platform, r) for r in sorted(rounds) if r < rnd]

    def _pop_round(self, platform: str, rnd: int) -> Dict[int, List[AgentActivity]]:
        with self._buffer_lock:
            return self._round_buffers.get(platform, {}).pop(rnd, {})

    def flush_round(self, round_num: int, platform: Optional[str] = None):
        """Flush every buffered round <= round_num (call on round_end).

        While the worker runs this only enqueues a marker, so the caller (the
        simulation monitor thread) never blocks on graph ingestion."""
        if self._running and self._worker_thread and self._worker_thread.is_alive():
            self._activity_queue.put(_FlushMarker(round_num, platform))
            return
        self._flush_rounds_upto(round_num, platform)

    def _flush_rounds_upto(self, round_num: int, platform: Optional[str] = None):
        with self._buffer_lock:
            keys = [(p, r) for p, rounds in self._round_buffers.items()
                    if platform is None or p == platform.lower()
                    for r in rounds if r <= round_num]
        for p, r in sorted(keys, key=lambda k: k[1]):
            self._send_round(p, r, self._pop_round(p, r))

    def _worker_loop(self):
        """后台工作循环 - 按轮次、按agent批量写入"""
        while self._running or not self._activity_queue.empty():
            try:
                try:
                    activity = self._activity_queue.get(timeout=1)
                except Empty:
                    continue
                if isinstance(activity, _FlushMarker):
                    self._flush_rounds_upto(activity.round_num, activity.platform)
                    continue
                for platform, rnd in self._buffer(activity):
                    self._send_round(platform, rnd, self._pop_round(platform, rnd))
            except Exception as e:
                logger.error(f"工作循环异常: {e}")
                time.sleep(1)

    def _send_round(self, platform: str, round_num: int,
                    by_agent: Dict[int, List[AgentActivity]]):
        """One add_batch per round: one episode per agent in that agent's group."""
        if not by_agent:
            return
        ref = memory_compat.reference_time_for_round(self.graph_id, round_num)
        episodes = []
        n_items = 0
        for agent_id, acts in sorted(by_agent.items()):
            text = "\n".join(a.to_episode_text() for a in acts)
            n_items += len(acts)
            episodes.append(memory_compat.EpisodeData(
                data=text, type="text",
                group_id=memory_compat.agent_group(agent_id),
                reference_time=ref,
                name=f"{platform}_r{round_num}_agent{agent_id}",
                source_description=f"{platform} simulation round {round_num}",
            ))

        with self._send_lock:
            for attempt in range(self.MAX_RETRIES):
                try:
                    self.client.graph.add_batch(graph_id=self.graph_id, episodes=episodes)
                    self._total_sent += 1
                    self._episodes_sent += len(episodes)
                    self._total_items_sent += n_items
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"写入{display_name}第{round_num}轮: {len(episodes)} 个agent, "
                                f"{n_items} 条活动 -> 图谱 {self.graph_id}")
                    return
                except memory_compat.FREEZE_ERRORS:
                    # Never retry a freeze violation: it is a bug, not a transient error.
                    self._failed_count += 1
                    raise
                except Exception as e:
                    if attempt < self.MAX_RETRIES - 1:
                        logger.warning(f"批量写入失败 (尝试 {attempt + 1}/{self.MAX_RETRIES}): {e}")
                        time.sleep(self.RETRY_DELAY * (attempt + 1))
                    else:
                        logger.error(f"批量写入失败，已重试{self.MAX_RETRIES}次: {e}")
                        self._failed_count += 1

    def _flush_remaining(self):
        """写入队列和缓冲区中剩余的活动"""
        while not self._activity_queue.empty():
            try:
                item = self._activity_queue.get_nowait()
            except Empty:
                break
            if not isinstance(item, _FlushMarker):
                self._buffer(item)
        with self._buffer_lock:
            keys = [(p, r) for p, rounds in self._round_buffers.items() for r in rounds]
        for p, r in sorted(keys, key=lambda k: k[1]):
            try:
                self._send_round(p, r, self._pop_round(p, r))
            except Exception as e:
                logger.error(f"写入剩余活动失败: platform={p}, round={r}: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._buffer_lock:
            buffer_sizes = {p: sum(len(a) for agents in rounds.values() for a in agents.values())
                            for p, rounds in self._round_buffers.items()}

        return {
            "graph_id": self.graph_id,
            "total_activities": self._total_activities,
            "batches_sent": self._total_sent,
            "episodes_sent": self._episodes_sent,
            "items_sent": self._total_items_sent,
            "failed_count": self._failed_count,
            "skipped_count": self._skipped_count,
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """
    管理多个模拟的Zep图谱记忆更新器
    
    每个模拟可以有自己的更新器实例
    """
    
    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> ZepGraphMemoryUpdater:
        """
        为模拟创建图谱记忆更新器
        
        Args:
            simulation_id: 模拟ID
            graph_id: Zep图谱ID
            
        Returns:
            ZepGraphMemoryUpdater实例
        """
        with cls._lock:
            # 如果已存在，先停止旧的
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            
            updater = ZepGraphMemoryUpdater(graph_id)
            updater.start()
            cls._updaters[simulation_id] = updater
            
            logger.info(f"创建图谱记忆更新器: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """获取模拟的更新器"""
        return cls._updaters.get(simulation_id)
    
    @classmethod
    def stop_updater(cls, simulation_id: str):
        """停止并移除模拟的更新器"""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"已停止图谱记忆更新器: simulation_id={simulation_id}")
    
    # 防止 stop_all 重复调用的标志
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """停止所有更新器"""
        # 防止重复调用
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"停止更新器失败: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("已停止所有图谱记忆更新器")
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """获取所有更新器的统计信息"""
        return {
            sim_id: updater.get_stats() 
            for sim_id, updater in cls._updaters.items()
        }
