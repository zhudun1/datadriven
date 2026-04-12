-- =====================================
-- 数据驱动编排系统 MySQL 初始化脚本
-- 三个数据库：qos_user_center / business_awareness / intelligent_orchestration
-- =====================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- =====================================
-- 1. qos_user_center（用户中心）
-- =====================================
CREATE DATABASE IF NOT EXISTS qos_user_center DEFAULT CHARACTER SET utf8mb4;
USE qos_user_center;

DROP TABLE IF EXISTS t_user;
CREATE TABLE t_user (
    user_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(256) NOT NULL,
    role ENUM('sys-admin','net-ops','algo-engineer','line-security','user') NOT NULL DEFAULT 'user',
    last_login TIMESTAMP NULL DEFAULT NULL,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO t_user (username, password_hash, role) VALUES
('admin', 'admin123', 'sys-admin'),
('demo@test.com', 'demo123', 'user');

-- =====================================
-- 2. business_awareness（业务感知）
-- =====================================
CREATE DATABASE IF NOT EXISTS business_awareness DEFAULT CHARACTER SET utf8mb4;
USE business_awareness;

DROP TABLE IF EXISTS t_industrial_data;
CREATE TABLE t_industrial_data (
    data_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rgb_path VARCHAR(512) DEFAULT NULL,
    pcd_path VARCHAR(512) DEFAULT NULL,
    is_processed TINYINT DEFAULT 0,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (rgb_path IS NOT NULL OR pcd_path IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS t_perception_log;
CREATE TABLE t_perception_log (
    log_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    data_id BIGINT DEFAULT NULL,
    anomaly_score FLOAT DEFAULT 0,
    risk_level VARCHAR(32) DEFAULT '',
    qos_vector TEXT,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS t_mapping_rules;
CREATE TABLE t_mapping_rules (
    rule_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    risk_level VARCHAR(32) NOT NULL,
    sensitivity VARCHAR(32) NOT NULL,
    priority INT DEFAULT 0,
    qos_bandwidth FLOAT DEFAULT 0,
    qos_latency FLOAT DEFAULT 0,
    qos_priority INT DEFAULT 0,
    qos_loss_rate FLOAT DEFAULT 0,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =====================================
-- 3. intelligent_orchestration（智能编排）
-- =====================================
CREATE DATABASE IF NOT EXISTS intelligent_orchestration DEFAULT CHARACTER SET utf8mb4;
USE intelligent_orchestration;

DROP TABLE IF EXISTS t_orchestration_log;
CREATE TABLE t_orchestration_log (
    log_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    data_id BIGINT DEFAULT NULL,
    risk_snapshot JSON DEFAULT NULL,
    decision_plan JSON DEFAULT NULL,
    expected_reward FLOAT DEFAULT 0,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS t_resource_inventory;
CREATE TABLE t_resource_inventory (
    resource_id VARCHAR(64) PRIMARY KEY,
    resource_name VARCHAR(128) DEFAULT NULL,
    resource_type ENUM('compute','network') DEFAULT 'compute',
    is_active TINYINT DEFAULT 1,
    current_state JSON DEFAULT NULL,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS t_alarms;
CREATE TABLE t_alarms (
    alarm_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    level VARCHAR(16) DEFAULT 'INFO',
    content TEXT DEFAULT NULL,
    source_module VARCHAR(64) DEFAULT NULL,
    is_read TINYINT DEFAULT 0,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 资源操作日志表（记录增/删操作）
DROP TABLE IF EXISTS t_resource_operations;
CREATE TABLE t_resource_operations (
    operation_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    operation_type ENUM('add_node','delete_node','add_link','delete_link') NOT NULL,
    resource_id VARCHAR(64) NOT NULL,
    previous_state JSON DEFAULT NULL,
    operator VARCHAR(64) DEFAULT NULL,
    status ENUM('pending','completed','failed') DEFAULT 'pending',
    error_message TEXT DEFAULT NULL,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    complete_time TIMESTAMP NULL DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =====================================
-- 初始化资源数据（5节点 + 8链路）
-- =====================================
USE intelligent_orchestration;

-- 5个计算节点
INSERT INTO t_resource_inventory (resource_id, resource_name, resource_type, is_active, current_state) VALUES
('node-0', '计算节点-0', 'compute', 1, '{"cpu":0.6,"memory":0.9,"energy_consumption":0.2,"vcpu":16,"memory_gb":64,"storage":500,"bandwidth":10000}'),
('node-1', '计算节点-1', 'compute', 1, '{"cpu":0.8,"memory":0.75,"energy_consumption":0.35,"vcpu":32,"memory_gb":128,"storage":1000,"bandwidth":20000}'),
('node-2', '计算节点-2', 'compute', 1, '{"cpu":0.7,"memory":0.5,"energy_consumption":0.5,"vcpu":24,"memory_gb":64,"storage":500,"bandwidth":8000}'),
('node-3', '计算节点-3', 'compute', 1, '{"cpu":0.95,"memory":0.9,"energy_consumption":0.25,"vcpu":48,"memory_gb":256,"storage":2000,"bandwidth":40000}'),
('node-4', '边缘节点-4', 'compute', 1, '{"cpu":0.5,"memory":0.3,"energy_consumption":0.8,"vcpu":8,"memory_gb":16,"storage":100,"bandwidth":2000}');

-- 8条网络链路
INSERT INTO t_resource_inventory (resource_id, resource_name, resource_type, is_active, current_state) VALUES
('link-0', '链路-0 (节点0-节点1)', 'network', 1, '{"bandwidth":1.2,"latency":5,"path_id":0,"src":0,"dst":1}'),
('link-1', '链路-1 (节点1-节点3)', 'network', 1, '{"bandwidth":1.1,"latency":10,"path_id":0,"src":1,"dst":3}'),
('link-2', '链路-2 (节点1-节点2)', 'network', 1, '{"bandwidth":0.8,"latency":30,"path_id":1,"src":1,"dst":2}'),
('link-3', '链路-3 (节点3-节点2)', 'network', 1, '{"bandwidth":0.9,"latency":25,"path_id":1,"src":3,"dst":2}'),
('link-4', '链路-4 (节点2-节点4)', 'network', 1, '{"bandwidth":0.5,"latency":80,"path_id":2,"src":2,"dst":4}'),
('link-5', '链路-5 (节点3-节点4)', 'network', 1, '{"bandwidth":0.6,"latency":50,"path_id":2,"src":3,"dst":4}'),
('link-6', '链路-6 (节点0-节点3)', 'network', 1, '{"bandwidth":1.0,"latency":15,"path_id":3,"src":0,"dst":3}'),
('link-7', '链路-7 (节点3-节点4b)', 'network', 1, '{"bandwidth":0.6,"latency":50,"path_id":3,"src":3,"dst":4}');

SET FOREIGN_KEY_CHECKS = 1;