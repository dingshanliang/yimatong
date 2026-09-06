import { Typography } from "antd";

const { Text } = Typography;
import type { ChannelsWorkspaceApi } from "./useChannelsWorkspace";
import { Select, Space, Table } from "antd";
import type { ChannelAccess } from "@/lib/channel-access";

interface TabProps {
  w: ChannelsWorkspaceApi;
  access: ChannelAccess;
}

export function DiversionTab({ w }: TabProps) {
  const {
    clues,
    loading,
    clueResolvedFilter,
    setClueResolvedFilter,
    clueSeverityFilter,
    setClueSeverityFilter,
    clueColumns,
  } = w;
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Space wrap>
          <Select
            aria-label="线索状态"
            value={clueResolvedFilter}
            className="w-36"
            options={[
              { label: "只看待处理", value: "pending" },
              { label: "已处理", value: "resolved" },
              { label: "全部线索", value: "all" },
            ]}
            onChange={setClueResolvedFilter}
          />
          <Select
            aria-label="风险等级"
            allowClear
            placeholder="风险等级"
            className="w-32"
            value={clueSeverityFilter}
            options={[
              { label: "高风险", value: "high" },
              { label: "中风险", value: "medium" },
              { label: "低风险", value: "low" },
            ]}
            onChange={setClueSeverityFilter}
          />
        </Space>
        <Text type="secondary">优先处理跨省、跨大区扫码线索</Text>
      </div>
      <Table
        columns={clueColumns}
        dataSource={clues.items}
        rowKey="id"
        loading={loading}
      />
    </>
  );
}
