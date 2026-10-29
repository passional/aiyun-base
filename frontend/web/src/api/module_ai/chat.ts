import { request } from "@utils";

const API_PATH = "/ai/chat";

/** 会话分页列表查询（列表接口） */
export interface ChatSessionListQuery extends PageQuery {
  title?: string;
  created_at?: string[];
  updated_at?: string[];
}

export const AiChatAPI = {
  getSessionList(query: ChatSessionListQuery) {
    return request<ApiResponse<PageResult<ChatSession>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  createSession(body: { title: string }) {
    return request<ApiResponse<ChatSession>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateSession(id: string, body: { title: string }) {
    return request<ApiResponse<ChatSession>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteSession(body: string[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  chat(body: { message: string; session_id?: string | null }) {
    return request<ApiResponse<AiChatResponse>>({
      url: `${API_PATH}/ai-chat`,
      method: "post",
      data: body,
    });
  },

  getSessionDetail(sessionId: string) {
    return request<ApiResponse<ChatSessionDetail>>({
      url: `${API_PATH}/detail/${sessionId}`,
      method: "get",
    });
  },

  // ============ AI 模型配置 ============ //
  getModelConfig() {
    return request<ApiResponse<AiModelConfigList>>({
      url: `${API_PATH}/model`,
      method: "get",
    });
  },

  createModelConfig(body: AiModelConfigInput) {
    return request<ApiResponse<AiModelConfigItem>>({
      url: `${API_PATH}/model`,
      method: "post",
      data: body,
    });
  },

  updateModelConfig(id: string, body: AiModelConfigInput) {
    return request<ApiResponse<AiModelConfigItem>>({
      url: `${API_PATH}/model/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteModelConfig(id: string) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/model/${id}`,
      method: "delete",
    });
  },

  activateModelConfig(id: string) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/model/${id || "__default__"}/activate`,
      method: "post",
    });
  },

  /** 拉取接口可用模型列表（按填写中的 base_url + api_key 探测，不落库） */
  listAvailableModels(body: { base_url: string; api_key: string }) {
    return request<ApiResponse<AiModelOption[]>>({
      url: `${API_PATH}/model/available`,
      method: "post",
      data: body,
    });
  },

  /** 测试接口连通性（按填写中的参数探测，不落库） */
  testModelConnection(body: { base_url: string; api_key: string; model_id: string }) {
    return request<ApiResponse<AiModelTestResult>>({
      url: `${API_PATH}/model/test`,
      method: "post",
      data: body,
    });
  },
};

export interface AiModelOption {
  id: string;
  owned_by?: string | null;
}

export interface AiModelTestResult {
  ok: boolean;
  model_id: string;
  latency_ms: number;
}

export interface AiModelConfigInput {
  name: string;
  base_url: string;
  api_key: string;
  model_id: string;
  temperature: number;
}

export interface AiModelConfigItem extends AiModelConfigInput {
  id: string;
  created_time: string | null;
}

export interface AiModelConfigList {
  items: AiModelConfigItem[];
  active_id: string | null;
}

export default AiChatAPI;

export interface ChatSessionMessage {
  id: string;
  role: string;
  content: string;
  created_at: number | null;
}

export interface ChatSession {
  session_id: string;
  agent_id: string | null;
  team_id: string | null;
  team_name: string | null;
  workflow_id: string | null;
  user_id: string | null;
  session_data: Record<string, any> | null;
  agent_data: Record<string, any> | null;
  team_data: Record<string, any> | null;
  workflow_data: Record<string, any> | null;
  metadata: Record<string, any> | null;
  runs: Array<Record<string, any>> | null;
  summary: Record<string, any> | null;
  created_at: number | null;
  updated_at: number | null;

  id: string;
  title: string | null;
  created_time: string | null;
  updated_time: string | null;
  message_count: number;
  messages: ChatSessionMessage[];
}

export interface SessionGroup {
  id: string;
  title: string;
  sessions: ChatSession[];
}

export interface UserInfo {
  id: number;
  name: string;
  username: string;
  avatar: string;
  email: string;
}

export interface AiChatResponse {
  response: string;
  session_id: string;
  function_calls: Array<{
    name: string;
    arguments: Record<string, any>;
  }> | null;
  action: Record<string, any> | null;
}

export interface ChatSessionDetail {
  session_id: string;
  agent_id: string | null;
  team_id: string | null;
  team_name: string | null;
  workflow_id: string | null;
  user_id: string | null;
  session_data: Record<string, any> | null;
  agent_data: Record<string, any> | null;
  team_data: Record<string, any> | null;
  workflow_data: Record<string, any> | null;
  metadata: Record<string, any> | null;
  runs: Array<Record<string, any>> | null;
  summary: Record<string, any> | null;
  created_at: number | null;
  updated_at: number | null;

  id: string;
  title: string | null;
  created_time: string | null;
  updated_time: string | null;
  message_count: number;
  messages: ChatSessionMessage[];
}
