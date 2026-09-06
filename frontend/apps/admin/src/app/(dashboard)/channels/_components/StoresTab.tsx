import type { ChannelsWorkspaceApi } from "./useChannelsWorkspace";
import { Button, Table } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ChannelAccess } from "@/lib/channel-access";

interface TabProps {
  w: ChannelsWorkspaceApi;
  access: ChannelAccess;
}

export function StoresTab({ w, access }: TabProps) {
  const { stores, loading, openEntityModal, storeColumns } = w;
  return (
    <>
      {access.canManage && (
        <div className="mb-4 flex justify-end">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => openEntityModal("store")}
          >
            新建门店
          </Button>
        </div>
      )}
      <Table
        columns={storeColumns}
        dataSource={stores.items}
        rowKey="id"
        loading={loading}
      />
    </>
  );
}
