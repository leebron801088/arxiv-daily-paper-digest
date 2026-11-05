"""Five subjects and their discovery terms for the daily PDF edition."""
from __future__ import annotations

import re

VAD = 'video_anomaly_detection'
DAILY_TOPIC = 'daily_research'
PAPERS_PER_TOPIC = 3
# Title/abstract search is deliberately broader than the editorial scope.
TOPICS = {
    VAD: dict(title='视频异常检测',
              focus='异常识别与定位、细粒度时序、长视频与音视证据。上游方法须有明确的迁移路径。',
              terms=('video anomaly detection', 'video anomaly understanding', 'video anomaly',
                     'abnormal event detection', 'traffic anomaly reasoning'),
              rss=r'\b(video|surveillance|cctv)\b.*\b(anomal\w*|abnormal\w*)\b|\b(anomal\w*|abnormal\w*)\b.*\b(video|surveillance|cctv)\b'),
    'human_mesh_pose_recovery': dict(title='人体网格恢复 / 姿态估计 / 动作捕捉',
              focus='人体网格、三维人体关键点、运动捕捉与时空一致性。',
              terms=('human mesh recovery', 'human mesh reconstruction', '3d human pose estimation',
                     'human motion capture', 'monocular human reconstruction', 'whole-body pose estimation'),
              rss=r'human mesh|human pose|human motion capture|whole.body pose|\bSMPL(?:-X)?\b'),
    'human_motion_generation': dict(title='人体动作生成 / 动作编辑',
              focus='可控人体动作生成与编辑、文本到动作、人-物-场景交互。',
              terms=('human motion generation', 'human motion editing', 'text-to-motion', 'motion synthesis',
                     'human-object interaction generation', 'motion retargeting'),
              rss=r'human motion generation|motion edit|text.to.motion|motion synthesis|motion retargeting'),
    'motor_fault_diagnosis': dict(title='电机故障诊断',
              focus='优先电机、轴承和旋转机械的故障识别；工业异常方法须明确传感器或工况迁移边界。',
              terms=('motor fault diagnosis', 'bearing fault diagnosis', 'rotating machinery fault diagnosis',
                     'induction motor fault', 'bearing fault detection', 'rotating machinery fault'),
              rss=r'\b(?:motor|bearing|rotating machinery)\b.*\bfault\w*\b|\bfault\w*\b.*\b(?:motor|bearing|rotating machinery)\b'),
    'spatial_intelligence_world_models': dict(title='三维视觉 / 空间智能 / 世界模型',
              focus='三维或四维重建与表示、可控视角、具有空间状态或几何约束的世界模型。',
              terms=('spatial intelligence', '3d scene reconstruction', '4d scene reconstruction',
                     'dynamic gaussian splatting', '3d world model', 'spatial world model', 'camera trajectory editing'),
              rss=r'spatial intelligence|3d scene reconstruction|4d scene|dynamic gaussian|3d world model|spatial world model|camera trajectory edit'),
}


def query_for(topic):
    return ' OR '.join(f'(ti:"{term}" OR abs:"{term}")' for term in TOPICS[topic]['terms'])


def matches_feed(topic, text):
    return bool(re.search(TOPICS[topic]['rss'], text, re.I))
