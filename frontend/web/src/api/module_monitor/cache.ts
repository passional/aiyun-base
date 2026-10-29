import { request } from "@utils";

const API_PATH = "/monitor/cache";

const CacheAPI = {
  getCacheInfo() {
    return request<ApiResponse>({
      url: `${API_PATH}/info`,
      method: "get",
    });
  },

  getCacheNames() {
    return request<ApiResponse>({
      url: `${API_PATH}/get/names`,
      method: "get",
    });
  },

  getCacheKeys(cacheName: string) {
    return request<ApiResponse>({
      url: `${API_PATH}/get/keys/${cacheName}`,
      method: "get",
    });
  },

  getCacheValue(cacheName: string, cacheKey: string) {
    return request<ApiResponse>({
      url: `${API_PATH}/get/value/${cacheName}/${cacheKey}`,
      method: "get",
    });
  },

  /** Redis 逻辑库列表（只读） */
  getRedisDatabases() {
    return request<ApiResponse<RedisDatabase[]>>({
      url: `${API_PATH}/redis/databases`,
      method: "get",
    });
  },

  /** SCAN 浏览 Redis 键（只读） */
  scanRedisKeys(params: { db?: number; pattern?: string; cursor?: number; count?: number }) {
    return request<ApiResponse<RedisKeyPage>>({
      url: `${API_PATH}/redis/keys`,
      method: "get",
      params,
    });
  },

  /** 读取 Redis 单键详情（只读；敏感键已脱敏） */
  getRedisValue(params: { db?: number; key: string }) {
    return request<ApiResponse<RedisValue>>({
      url: `${API_PATH}/redis/value`,
      method: "get",
      params,
    });
  },

  deleteCacheName(cacheName: string) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete/name/${cacheName}`,
      method: "delete",
    });
  },

  deleteCacheKey(cacheKey: string) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete/key/${cacheKey}`,
      method: "delete",
    });
  },

  deleteCacheAll() {
    return request<ApiResponse>({
      url: `${API_PATH}/clear`,
      method: "delete",
    });
  },
};

export default CacheAPI;

export interface CacheForm {
  cache_name: string;
  cache_key: string;
  cache_value: string;
}

export interface CacheInfo {
  cache_key: string;
  cache_name: string;
  cache_value: string;
  remark?: string;
}

export interface CommandStats {
  name: string;
  value: string;
}

export interface RedisInfo {
  redis_version: string;
  redis_mode: string;
  tcp_port: number;
  connected_clients: number;
  uptime_in_days: number;
  used_memory_human: string;
  used_cpu_user_children: string;
  maxmemory_human: string;
  aof_enabled: string;
  rdb_last_bgsave_status: string;
  instantaneous_input_kbps: number;
  instantaneous_output_kbps: number;
}

export interface CacheMonitor {
  command_stats: CommandStats[];
  db_size: number;
  info: RedisInfo;
}

export interface RedisDatabase {
  db: number;
  keys: number;
  expires: number;
}

export interface RedisKeyMeta {
  key: string;
  type: string;
  ttl: number;
}

export interface RedisKeyPage {
  cursor: number;
  keys: RedisKeyMeta[];
  scanned: number;
}

export interface RedisValue {
  key: string;
  type: string;
  ttl: number;
  size: number | null;
  value: unknown;
  truncated: boolean;
  masked: boolean;
}
