SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- =====================================
-- 1. 用户中心库
-- =====================================
DROP DATABASE IF EXISTS qos_user_center;
CREATE DATABASE qos_user_center DEFAULT CHARACTER SET utf8mb4;
USE qos_user_center;

DROP TABLE IF EXISTS t_user;

CREATE TABLE t_user (
    user_id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '用户唯一ID',
    username VARCHAR(64) NOT NULL UNIQUE COMMENT '登录用户名',
    password_hash VARCHAR(256) NOT NULL COMMENT '加密后的密码',
    role ENUM('sys-admin', 'net-ops', 'algo-engineer', 'line-security') NOT NULL COMMENT '角色',
    last_login TIMESTAMP NULL DEFAULT NULL COMMENT '最后登录时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户与权限表';

INSERT INTO t_user (username, password_hash, role)
VALUES ('admin', 'admin_test_hash', 'sys-admin');