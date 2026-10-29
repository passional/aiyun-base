import { request } from "@utils";

const API_PATH = "/ops/ssh";

export interface SshHost {
  id: number;
  name: string;
  host: string;
  port: number;
  username: string;
  auth_type: "password" | "key";
  status: number;
  description?: string | null;
  has_password: boolean;
  has_private_key: boolean;
}

export interface SshHostForm {
  name: string;
  host: string;
  port: number;
  username: string;
  auth_type: "password" | "key";
  password?: string;
  private_key?: string;
  passphrase?: string;
  status?: number;
  description?: string | null;
}

export interface SshSnippet {
  id: number;
  name: string;
  command: string;
  description?: string | null;
}

export interface TerminalSession {
  session_id: string;
  host_id: number;
  host_name: string;
  cols: number;
  rows: number;
  created_at: string;
}

export interface TerminalPoll {
  data: string;
  closed: boolean;
  exit_status: number | null;
}

export interface BatchExecResult {
  host_id: number;
  host_name: string;
  ok: boolean;
  exit_status: number | null;
  stdout: string;
  stderr: string;
  error: string;
  elapsed_ms: number;
}

const SshAPI = {
  listHosts() {
    return request<ApiResponse<SshHost[]>>({ url: `${API_PATH}/host/list`, method: "get" });
  },
  detailHost(id: number) {
    return request<ApiResponse<SshHost>>({ url: `${API_PATH}/host/detail/${id}`, method: "get" });
  },
  createHost(body: SshHostForm) {
    return request<ApiResponse<SshHost>>({
      url: `${API_PATH}/host/create`,
      method: "post",
      data: body,
    });
  },
  updateHost(id: number, body: SshHostForm) {
    return request<ApiResponse<SshHost>>({
      url: `${API_PATH}/host/update/${id}`,
      method: "put",
      data: body,
    });
  },
  deleteHosts(ids: number[]) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/host/delete`,
      method: "delete",
      data: ids,
    });
  },
  testHostConfig(body: SshHostForm) {
    return request<ApiResponse<boolean>>({
      url: `${API_PATH}/host/test`,
      method: "post",
      data: body,
    });
  },
  testHost(id: number) {
    return request<ApiResponse<boolean>>({ url: `${API_PATH}/host/test/${id}`, method: "post" });
  },
  listSnippets() {
    return request<ApiResponse<SshSnippet[]>>({ url: `${API_PATH}/snippet/list`, method: "get" });
  },
  createSnippet(body: { name: string; command: string; description?: string | null }) {
    return request<ApiResponse<SshSnippet>>({
      url: `${API_PATH}/snippet/create`,
      method: "post",
      data: body,
    });
  },
  updateSnippet(id: number, body: { name: string; command: string; description?: string | null }) {
    return request<ApiResponse<SshSnippet>>({
      url: `${API_PATH}/snippet/update/${id}`,
      method: "put",
      data: body,
    });
  },
  deleteSnippets(ids: number[]) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/snippet/delete`,
      method: "delete",
      data: ids,
    });
  },
  openTerminal(body: { host_id: number; cols: number; rows: number }) {
    return request<ApiResponse<TerminalSession>>({
      url: `${API_PATH}/terminal/open`,
      method: "post",
      data: body,
    });
  },
  openLog(body: { host_id: number; path: string; lines: number }) {
    return request<ApiResponse<TerminalSession>>({
      url: `${API_PATH}/log/open`,
      method: "post",
      data: body,
    });
  },
  pollTerminal(sessionId: string, timeout = 1.5) {
    return request<ApiResponse<TerminalPoll>>({
      url: `${API_PATH}/terminal/${sessionId}/poll`,
      method: "get",
      params: { timeout },
    });
  },
  inputTerminal(sessionId: string, data: string) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/terminal/${sessionId}/input`,
      method: "post",
      data: { data },
    });
  },
  resizeTerminal(sessionId: string, cols: number, rows: number) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/terminal/${sessionId}/resize`,
      method: "post",
      data: { cols, rows },
    });
  },
  closeTerminal(sessionId: string) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/terminal/${sessionId}`,
      method: "delete",
    });
  },
  batchExec(body: { host_ids: number[]; command: string; timeout?: number }) {
    return request<ApiResponse<BatchExecResult[]>>({
      url: `${API_PATH}/batch-exec`,
      method: "post",
      data: body,
    });
  },
};

export default SshAPI;
