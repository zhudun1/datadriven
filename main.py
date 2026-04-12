import torch
import os
import sys
import numpy as np
import json
from pathlib import Path


sys.path.append(os.path.dirname(os.path.abspath(__file__)))


from business_perception.cfm_detector import CFMDetector
from business_perception.qos_translator import QoSTranslator
from intelligent_orchestration.ppo_agent import GraphPPOOrchestrator
from intelligent_orchestration.vne_env import GraphVNEEnv
from common.utils import init_logger
from models.dataset import get_data_loader  


CLASS_NAME = "cable_gland"
BASE_PATH = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_PATH = os.path.join(BASE_PATH, "checkpoints/checkpoints_CFM_mvtec")
DATASET_PATH = os.path.join(BASE_PATH, "datasets/mvtec3d")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# 测试配置
TEST_ANOMALY_TYPES = ["bent"] # 或者 "all"

# PPO配置
PPO_TRAIN_TIMESTEPS = 200000  # 增加训练步数
PPO_TRAIN_FLAG = True

# 初始化日志
logger = init_logger()

def process_sample_by_loader(detector, translator, ppo_agent, env, rgb, pc, defect_type, sample_path):
    """
    处理单个样本的流水线：业务感知 -> 智能编排 -> 基础设施下发
    """
    logger.info(f"\n===== 处理【{defect_type}】类型 - 样本：{Path(sample_path).name} =====")
    
    try:
        # ===================== 1. 业务感知层 =====================
        logger.info("--- 【业务感知层】开始 ---")
        # 计算异常分数
        anomaly_score = detector.get_anomaly_score(rgb, pc)
        # 映射为QoS需求
        risk_level, qos_vector = translator.translate(anomaly_score)
        
        logger.info(f"异常分数：{anomaly_score:.4f} | 风险等级：{risk_level}")
        logger.info(f"QoS需求向量：{qos_vector.round(4)}")
        logger.info("--- 【业务感知层】结束 ---")

        # ===================== 2. 智能编排层 =====================
        logger.info("--- 【智能编排层】开始 ---")
        # 重置环境并将感知到的QoS注入VNR
        obs, info = env.reset()
        env.set_qos_to_vnr(qos_vector)
        obs = env._get_obs()

        # PPO 预测决策
        action_dict = ppo_agent.predict(obs)
        
        # 动作校验
        if not isinstance(action_dict, dict) or "vnf_node" not in action_dict:
            raise ValueError(f"动作格式异常: {action_dict}")

        logger.info(f"PPO决策：VNF映射{action_dict['vnf_node']}")

        # 环境执行动作
        obs, reward, terminated, truncated, info = env.step(action_dict)
        logger.info(f"编排奖励：{reward:.2f} | QoS达标：{info['qos_ok']} | 资源充足：{info['resource_ok']}")
        logger.info("--- 【智能编排层】结束 ---")

        # ===================== 3. 基础设施层 =====================
        logger.info("--- 【基础设施层】开始 ---")
        # 模拟部署结果（跳过kubernetes和SDN调用）
        logger.info("VNF部署模拟: 节点映射成功")
        logger.info("SDN流表模拟: 链路自动计算")
        logger.info("--- 【基础设施层】结束 ---")

        return {
            "defect_type": defect_type,
            "sample_name": Path(sample_path).name,
            "anomaly_score": round(float(anomaly_score), 4),
            "risk_level": risk_level,
            "mapped_nodes": [int(n) for n in action_dict["vnf_node"]],
            "mapped_links": [],  # 不再输出链路信息
            "reward": round(float(reward), 2),
            "qos_ok": bool(info["qos_ok"]),
            "resource_ok": bool(info["resource_ok"]),
            "error": None
        }
    except Exception as e:
        logger.error(f"处理样本出错：{str(e)}", exc_info=True)
        return {
            "defect_type": defect_type,
            "sample_name": Path(sample_path).name,
            "mapped_nodes": [],
            "mapped_links": [],
            "qos_ok": False,
            "resource_ok": False,
            "error": str(e)
        }

def main():
    logger.info("===== 启动异常检测与资源编排系统 =====")
    
    # 1. 全局初始化感知层
    detector = CFMDetector(CLASS_NAME, CHECKPOINT_PATH, DEVICE)
    translator = QoSTranslator()

    # 2. 全局初始化编排层
    env = GraphVNEEnv()
    ppo_agent = GraphPPOOrchestrator(env) 

    # 3. PPO 预热训练
    if PPO_TRAIN_FLAG:
        ppo_agent.train_warmup(timesteps=PPO_TRAIN_TIMESTEPS)

    # 4. 加载测试数据
    test_loader = get_data_loader(
        split="test",
        class_name=CLASS_NAME,
        img_size=224,
        dataset_path=DATASET_PATH
    )

    # 5. 遍历测试集执行闭环流程
    all_results = []
    for (rgb, pc, depth), gt, label, rgb_path in test_loader:
        # 从路径解析当前样本类型（兼容Windows和Linux路径）
        path_str = rgb_path[0].replace('\\', '/')
        defect_type = path_str.split('/')[-3]
        
        # 过滤类型
        if TEST_ANOMALY_TYPES != "all" and defect_type not in TEST_ANOMALY_TYPES:
            continue
        
        # 执行处理逻辑
        result = process_sample_by_loader(
            detector, translator, ppo_agent, env, 
            rgb, pc, defect_type, rgb_path[0]
        )
        all_results.append(result)

    # 6. 输出汇总报告与生成策略描述文件
    logger.info("\n===== 测试汇总报告 =====")
    total_samples = len(all_results)
    qos_success_count = sum(1 for r in all_results if r.get("qos_ok") is True)
    resource_success_count = sum(1 for r in all_results if r.get("resource_ok") is True)
    
    qos_rate = (qos_success_count / total_samples * 100) if total_samples > 0 else 0
    res_rate = (resource_success_count / total_samples * 100) if total_samples > 0 else 0

    type_summary = {}
    for res in all_results:
        dt = res["defect_type"]
        if dt not in type_summary:
            type_summary[dt] = {"total": 0, "success": 0, "avg_score": 0.0, "risk_dist": {}}
        
        type_summary[dt]["total"] += 1
        if res.get("error") is None:
            type_summary[dt]["success"] += 1
            type_summary[dt]["avg_score"] += res["anomaly_score"]
            rl = res["risk_level"]
            type_summary[dt]["risk_dist"][rl] = type_summary[dt]["risk_dist"].get(rl, 0) + 1

    for dt, summary in type_summary.items():
        logger.info(f"\n--- 异常类型：{dt} ---")
        logger.info(f"  总样本数：{summary['total']}")
        logger.info(f"  处理成功：{summary['success']}")
        if summary["success"] > 0:
            logger.info(f"  平均异常分数：{summary['avg_score']/summary['success']:.4f}")
            logger.info(f"  风险等级分布：{summary['risk_dist']}")

    # 生成 JSON 描述文件
    report_data = {
        "overall_stats": {
            "total_samples": total_samples,
            "qos_satisfaction_rate": f"{qos_rate:.2f}%",
            "resource_availability_rate": f"{res_rate:.2f}%"
        },
        "policy_details": all_results
    }
    
    with open("orchestration_policy_report.json", "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=4, ensure_ascii=False)

    logger.info("\n" + "="*30)
    logger.info(f"QoS 总达标率: {qos_rate:.2f}%")
    logger.info(f"资源总充足率: {res_rate:.2f}%")
    logger.info(f"策略描述文件已保存至: orchestration_policy_report.json")
    logger.info("===== 测试闭环完成 =====")

if __name__ == "__main__":
    main()