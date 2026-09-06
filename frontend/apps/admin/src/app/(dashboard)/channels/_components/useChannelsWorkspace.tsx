import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { App, Button, Form, Space, Switch, Tag, Typography } from "antd";
import { CheckOutlined, EditOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import type {
  Account,
  AccountScope,
  Allocation,
  Batch,
  Distributor,
  DiversionClue,
  DiversionInvestigation,
  Overview,
  PageResult,
  Region,
  Store,
} from "./shared";
import {
  batchCapacity,
  buildRegionInitialValues,
  buildRegionPayload,
  coverageTypeLabel,
  emptyPage,
  formatDateTime,
  productSkuLabel,
  regionCoverageLabel,
  severityTag,
} from "./shared";
import type { ChannelAccess } from "@/lib/channel-access";

const { Text } = Typography;

export function useChannelsWorkspace(access: ChannelAccess) {
  const appApi = App.useApp();
  const messageRef = useRef(appApi.message);
  const mutationKeysRef = useRef(new Map<string, string>());
  const autoRegionNameRef = useRef<string | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [distributors, setDistributors] =
    useState<PageResult<Distributor>>(emptyPage);
  const [regions, setRegions] = useState<PageResult<Region>>(emptyPage);
  const [stores, setStores] = useState<PageResult<Store>>(emptyPage);
  const [allocations, setAllocations] =
    useState<PageResult<Allocation>>(emptyPage);
  const [clues, setClues] = useState<PageResult<DiversionClue>>(emptyPage);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [scopes, setScopes] = useState<AccountScope[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState("distributors");

  const channelMutationConfig = (intent: string) => {
    let key = mutationKeysRef.current.get(intent);
    if (!key) {
      key = crypto.randomUUID();
      mutationKeysRef.current.set(intent, key);
    }
    return { headers: { "Idempotency-Key": key } };
  };

  const completeMutationIntent = (intent: string) => {
    mutationKeysRef.current.delete(intent);
  };

  const isConflictError = (err: unknown) =>
    typeof err === "object" &&
    err !== null &&
    (err as { response?: { status?: number } }).response?.status === 409;

  // 失败同样轮换幂等键：同 key 携带新 payload 重试会撞 409；
  // authority CAS 冲突（409）单独给出可行动提示并重拉最新数据。
  const failMutationIntent = (
    err: unknown,
    intent: string,
    fallback: string
  ) => {
    completeMutationIntent(intent);
    if (isConflictError(err)) {
      messageRef.current.warning(
        "记录已被他人更新，已为您刷新最新数据，请重试"
      );
      loadData();
    } else {
      messageRef.current.error(extractErrorMessage(err, fallback));
    }
  };
  const [tenantFeatures, setTenantFeatures] = useState<Record<string, boolean>>(
    {}
  );
  const [clueResolvedFilter, setClueResolvedFilter] = useState<
    "pending" | "resolved" | "all"
  >("pending");
  const [clueSeverityFilter, setClueSeverityFilter] = useState<
    string | undefined
  >();

  const [entityModal, setEntityModal] = useState<{
    type: "distributor" | "region" | "store";
    record?: Distributor | Region | Store;
    defaults?: Record<string, unknown>;
  } | null>(null);
  const [entitySaving, setEntitySaving] = useState(false);
  const [createdDistributor, setCreatedDistributor] =
    useState<Distributor | null>(null);
  const [createdRegion, setCreatedRegion] = useState<Region | null>(null);
  const [allocationOpen, setAllocationOpen] = useState(false);
  const [allocationToReassign, setAllocationToReassign] =
    useState<Allocation | null>(null);
  const [allocationToArchive, setAllocationToArchive] =
    useState<Allocation | null>(null);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [currentClue, setCurrentClue] = useState<DiversionClue | null>(null);
  const [investigation, setInvestigation] =
    useState<DiversionInvestigation | null>(null);
  const [entityForm] = Form.useForm();
  const [allocationForm] = Form.useForm();
  const [archiveAllocationForm] = Form.useForm();
  const [scopeForm] = Form.useForm();
  const [resolveForm] = Form.useForm();
  const [evidenceForm] = Form.useForm();
  const regionCoverageType = Form.useWatch("coverage_type", entityForm) as
    Region["coverage_type"] | undefined;
  const regionProvince = Form.useWatch("province", entityForm);
  const regionCity = Form.useWatch("city", entityForm);
  const regionCoverageProvinces = Form.useWatch(
    "coverage_provinces",
    entityForm
  ) as string[] | undefined;
  const allocationBatchId = Form.useWatch("batch_id", allocationForm);
  const allocationTargetType = Form.useWatch("target_type", allocationForm);
  const allocationRegionId = Form.useWatch("region_id", allocationForm);
  const allocationDistributorId = Form.useWatch(
    "distributor_id",
    allocationForm
  );
  const allocationStoreId = Form.useWatch("store_id", allocationForm);
  const allocationQuantity = Form.useWatch("quantity", allocationForm);
  const scopeRegionId = Form.useWatch("region_id", scopeForm);

  useEffect(() => {
    messageRef.current = appApi.message;
  }, [appApi.message]);

  useEffect(() => {
    api
      .get("/tenants/me")
      .then(({ data }) => {
        setTenantFeatures(data?.enabled_features || {});
      })
      .catch(() => {});
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const clueParams = new URLSearchParams();
      if (clueResolvedFilter !== "all") {
        clueParams.set(
          "resolved",
          clueResolvedFilter === "resolved" ? "true" : "false"
        );
      }
      if (clueSeverityFilter) {
        clueParams.set("severity", clueSeverityFilter);
      }
      const clueUrl = `/channels/diversion-clues${clueParams.toString() ? `?${clueParams.toString()}` : ""}`;
      const [
        overviewRes,
        distRes,
        regionRes,
        storeRes,
        allocRes,
        clueRes,
        batchRes,
        accountRes,
        scopeRes,
      ] = await Promise.all([
        api.get("/channels/overview"),
        api.get("/channels/distributors"),
        api.get("/channels/regions"),
        api.get("/channels/stores"),
        api.get("/channels/code-allocations", {
          params: { include_history: true },
        }),
        api.get(clueUrl),
        api.get("/code-batches"),
        access.canScope
          ? api.get("/accounts")
          : Promise.resolve({ data: { items: [] } }),
        access.canScope
          ? api.get("/channels/account-scopes")
          : Promise.resolve({ data: { items: [] } }),
      ]);
      setOverview(overviewRes.data);
      setDistributors(distRes.data);
      setRegions(regionRes.data);
      setStores(storeRes.data);
      setAllocations(allocRes.data);
      setClues(clueRes.data);
      setBatches(batchRes.data?.items ? batchRes.data.items : []);
      setAccounts(
        Array.isArray(accountRes.data)
          ? accountRes.data
          : accountRes.data?.items || []
      );
      setScopes(
        Array.isArray(scopeRes.data)
          ? scopeRes.data
          : scopeRes.data?.items || []
      );
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "加载渠道数据失败"));
    } finally {
      setLoading(false);
    }
  }, [access.canScope, clueResolvedFilter, clueSeverityFilter]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const selectedAllocationBatch = useMemo(
    () => batches.find((item) => item.id === allocationBatchId),
    [allocationBatchId, batches]
  );
  const selectedBatchCapacity = useMemo(
    () => batchCapacity(selectedAllocationBatch, allocations.items),
    [allocations.items, selectedAllocationBatch]
  );
  const allocationAvailableQuantity =
    selectedBatchCapacity.remaining + (allocationToReassign?.quantity || 0);

  useEffect(() => {
    if (!entityModal || createdDistributor || createdRegion) return;
    entityForm.resetFields();
    const initialValues =
      entityModal.type === "region"
        ? buildRegionInitialValues(entityModal.record, entityModal.defaults)
        : { ...(entityModal.record || {}), ...(entityModal.defaults || {}) };
    autoRegionNameRef.current = null;
    entityForm.setFieldsValue({
      ...initialValues,
      status: initialValues.status || "active",
    });
  }, [createdDistributor, createdRegion, entityForm, entityModal]);

  useEffect(() => {
    if (
      !entityModal ||
      entityModal.type !== "region" ||
      entityModal.record ||
      createdRegion
    )
      return;
    const coverageType = regionCoverageType || "city";
    const provinces = regionCoverageProvinces || [];
    const multiName =
      provinces.length >= 2
        ? `${provinces.slice(0, 2).join("、")}${provinces.length > 2 ? "等" : ""}大区`
        : "";
    const cityName = regionCity || regionProvince || "";
    const suggestedName =
      coverageType === "multi_province"
        ? multiName
        : coverageType === "province"
          ? regionProvince
            ? `${regionProvince}省区`
            : ""
          : cityName
            ? `${cityName}区域`
            : "";
    const currentName = entityForm.getFieldValue("name");
    if (
      suggestedName &&
      (!currentName || currentName === autoRegionNameRef.current)
    ) {
      entityForm.setFieldValue("name", suggestedName);
      autoRegionNameRef.current = suggestedName;
    }
  }, [
    createdRegion,
    entityForm,
    entityModal,
    regionCity,
    regionCoverageProvinces,
    regionCoverageType,
    regionProvince,
  ]);

  useEffect(() => {
    if (!allocationOpen) return;
    const targetType = allocationForm.getFieldValue("target_type") || "region";
    allocationForm.setFieldsValue({ target_type: targetType });
    if (!allocationForm.getFieldValue("batch_id") && batches[0]) {
      allocationForm.setFieldValue("batch_id", batches[0].id);
    }
    if (
      targetType === "region" &&
      !allocationForm.getFieldValue("region_id") &&
      regions.items[0]
    ) {
      allocationForm.setFieldValue("region_id", regions.items[0].id);
    }
  }, [allocationForm, allocationOpen, batches, regions.items]);

  useEffect(() => {
    if (!allocationOpen || allocationForm.getFieldValue("quantity")) return;
    if (selectedBatchCapacity.remaining > 0) {
      allocationForm.setFieldValue("quantity", 1);
    }
  }, [allocationForm, allocationOpen, selectedBatchCapacity.remaining]);

  useEffect(() => {
    if (!allocationRegionId) return;
    const region = regions.items.find((item) => item.id === allocationRegionId);
    if (region?.distributor_id) {
      allocationForm.setFieldValue("distributor_id", region.distributor_id);
    }
  }, [allocationForm, allocationRegionId, regions.items]);

  useEffect(() => {
    if (!scopeRegionId) return;
    const region = regions.items.find((item) => item.id === scopeRegionId);
    if (region?.distributor_id) {
      scopeForm.setFieldValue("distributor_id", region.distributor_id);
    }
  }, [regions.items, scopeForm, scopeRegionId]);

  useEffect(() => {
    if (!currentClue) return;
    resolveForm.setFieldsValue({
      resolution_action: currentClue.resolution_action || "contacted_channel",
      resolution_note: currentClue.resolution_note || "",
    });
  }, [currentClue, resolveForm]);

  const distributorOptions = useMemo(
    () =>
      distributors.items.map((item) => ({
        label: `${item.name} (${item.code})`,
        value: item.id,
      })),
    [distributors.items]
  );
  const regionOptions = useMemo(
    () =>
      regions.items.map((item) => ({
        label: `${item.name} / ${regionCoverageLabel(item)}`,
        value: item.id,
      })),
    [regions.items]
  );
  const storeOptions = useMemo(
    () =>
      stores.items.map((item) => ({
        label: `${item.name}${item.region_name ? ` / ${item.region_name}` : ""}${item.distributor_name ? ` / ${item.distributor_name}` : ""}`,
        value: item.id,
      })),
    [stores.items]
  );
  const selectedAllocationRegion = useMemo(
    () => regions.items.find((item) => item.id === allocationRegionId),
    [allocationRegionId, regions.items]
  );
  const selectedAllocationDistributor = useMemo(
    () =>
      distributors.items.find((item) => item.id === allocationDistributorId),
    [allocationDistributorId, distributors.items]
  );
  const selectedAllocationStore = useMemo(
    () => stores.items.find((item) => item.id === allocationStoreId),
    [allocationStoreId, stores.items]
  );
  const allocationTargetLabel = useMemo(() => {
    if (allocationTargetType === "store" && selectedAllocationStore) {
      return selectedAllocationStore.name;
    }
    if (
      allocationTargetType === "distributor" &&
      selectedAllocationDistributor
    ) {
      return selectedAllocationDistributor.name;
    }
    if (selectedAllocationRegion) {
      return `${selectedAllocationRegion.name} / ${regionCoverageLabel(selectedAllocationRegion)}`;
    }
    return "";
  }, [
    allocationTargetType,
    selectedAllocationDistributor,
    selectedAllocationRegion,
    selectedAllocationStore,
  ]);
  const allocationDistributorName =
    selectedAllocationDistributor?.name ||
    selectedAllocationRegion?.distributor_name ||
    selectedAllocationStore?.distributor_name ||
    "";
  const allocationSummary =
    selectedAllocationBatch && allocationTargetLabel && allocationQuantity
      ? `将 ${selectedAllocationBatch.batch_code} 的 ${allocationQuantity} 个已赋码货品登记到 ${allocationTargetLabel}${
          allocationDistributorName
            ? `，归属经销商 ${allocationDistributorName}`
            : ""
        }。`
      : "";

  const openEntityModal = (
    type: "distributor" | "region" | "store",
    record?: Distributor | Region | Store,
    defaults?: Record<string, unknown>
  ) => {
    setCreatedDistributor(null);
    setCreatedRegion(null);
    setEntityModal({ type, record, defaults });
  };

  const closeEntityModal = () => {
    setEntityModal(null);
    setCreatedDistributor(null);
    setCreatedRegion(null);
    setEntitySaving(false);
    entityForm.resetFields();
  };

  const saveEntity = async (values: Record<string, unknown>) => {
    if (!entityModal || !access.canManage) return;
    const mutationIntent = `entity:${entityModal.type}:${entityModal.record?.id || "create"}`;
    const mergedValues = { ...(entityModal.defaults || {}), ...values };
    const payload =
      entityModal.type === "region"
        ? buildRegionPayload(mergedValues)
        : mergedValues;
    const paths = {
      distributor: "/channels/distributors",
      region: "/channels/regions",
      store: "/channels/stores",
    };
    setEntitySaving(true);
    try {
      if (entityModal.record) {
        await api.patch(
          `${paths[entityModal.type]}/${entityModal.record.id}`,
          { ...payload, expected_version: entityModal.record.version },
          channelMutationConfig(mutationIntent)
        );
        messageRef.current.success("资料已更新");
        closeEntityModal();
      } else {
        const { data } = await api.post(
          paths[entityModal.type],
          payload,
          channelMutationConfig(mutationIntent)
        );
        messageRef.current.success(
          entityModal.type === "distributor" ? "经销商已创建" : "资料已创建"
        );
        if (entityModal.type === "distributor") {
          setCreatedDistributor(data);
          entityForm.resetFields();
        } else if (entityModal.type === "region") {
          setCreatedRegion(data);
          entityForm.resetFields();
        } else {
          closeEntityModal();
        }
      }
      completeMutationIntent(mutationIntent);
      loadData();
    } catch (err) {
      failMutationIntent(err, mutationIntent, "保存失败");
    } finally {
      setEntitySaving(false);
    }
  };

  const continueWithRegion = () => {
    if (!createdDistributor) return;
    const distributor = createdDistributor;
    closeEntityModal();
    setActiveTab("regions");
    openEntityModal("region", undefined, { distributor_id: distributor.id });
  };

  const continueWithScope = () => {
    if (!createdDistributor) return;
    const distributor = createdDistributor;
    closeEntityModal();
    setActiveTab("scopes");
    scopeForm.setFieldsValue({
      scope_type: "distributor",
      distributor_id: distributor.id,
    });
    setScopeOpen(true);
  };

  const continueWithAllocation = () => {
    closeEntityModal();
    setActiveTab("assign");
    if (createdRegion) {
      allocationForm.setFieldsValue({
        target_type: "region",
        region_id: createdRegion.id,
        distributor_id: createdRegion.distributor_id,
      });
    }
    setAllocationOpen(true);
  };

  const continueWithRegionScope = () => {
    if (!createdRegion) return;
    const region = createdRegion;
    closeEntityModal();
    setActiveTab("scopes");
    scopeForm.setFieldsValue({
      scope_type: "region",
      region_id: region.id,
      distributor_id: region.distributor_id,
    });
    setScopeOpen(true);
  };

  const continueWithStore = () => {
    if (!createdRegion) return;
    const region = createdRegion;
    closeEntityModal();
    setActiveTab("stores");
    openEntityModal("store", undefined, {
      region_id: region.id,
      distributor_id: region.distributor_id,
    });
  };

  const createAllocation = async (values: Record<string, unknown>) => {
    if (!access.canAllocate) return;
    const mutationIntent = allocationToReassign
      ? `allocation:reassign:${allocationToReassign.id}`
      : "allocation:create";
    try {
      if (allocationToReassign) {
        const reassignValues = { ...values };
        delete reassignValues.batch_id;
        await api.post(
          `/channels/code-allocations/${allocationToReassign.id}/reassign`,
          {
            ...reassignValues,
            expected_version: allocationToReassign.version,
          },
          channelMutationConfig(mutationIntent)
        );
      } else {
        await api.post(
          "/channels/code-allocations",
          values,
          channelMutationConfig(mutationIntent)
        );
      }
      completeMutationIntent(mutationIntent);
      messageRef.current.success(
        allocationToReassign ? "流向已重分配" : "流向已登记"
      );
      setAllocationOpen(false);
      setAllocationToReassign(null);
      allocationForm.resetFields();
      loadData();
    } catch (err) {
      failMutationIntent(err, mutationIntent, "分配失败");
    }
  };

  const openAllocationReassign = (record: Allocation) => {
    if (
      !access.canAllocate ||
      record.status !== "active" ||
      record.effective_to
    )
      return;
    setAllocationToReassign(record);
    allocationForm.setFieldsValue({
      batch_id: record.batch_id,
      target_type: record.target_type,
      distributor_id:
        record.target_type === "distributor"
          ? record.distributor_id
          : undefined,
      region_id: record.target_type === "region" ? record.region_id : undefined,
      store_id: record.target_type === "store" ? record.store_id : undefined,
      quantity: record.quantity,
      reason: undefined,
    });
    setAllocationOpen(true);
  };

  const archiveAllocation = async (values: { reason: string }) => {
    const record = allocationToArchive;
    if (
      !record ||
      !access.canAllocate ||
      record.status !== "active" ||
      record.effective_to
    )
      return;
    const mutationIntent = `allocation:archive:${record.id}`;
    try {
      await api.post(
        `/channels/code-allocations/${record.id}/archive`,
        { expected_version: record.version, reason: values.reason },
        channelMutationConfig(mutationIntent)
      );
      completeMutationIntent(mutationIntent);
      messageRef.current.success("流向已归档");
      setAllocationToArchive(null);
      archiveAllocationForm.resetFields();
      loadData();
    } catch (err) {
      failMutationIntent(err, mutationIntent, "归档失败");
    }
  };

  const createScope = async (values: Record<string, unknown>) => {
    if (!access.canScope) return;
    const mutationIntent = "scope:create";
    try {
      await api.post(
        "/channels/account-scopes",
        values,
        channelMutationConfig(mutationIntent)
      );
      completeMutationIntent(mutationIntent);
      messageRef.current.success("账号范围已绑定");
      setScopeOpen(false);
      scopeForm.resetFields();
      loadData();
    } catch (err) {
      failMutationIntent(err, mutationIntent, "绑定失败");
    }
  };

  const deleteScope = async (scope: AccountScope) => {
    if (!access.canScope) return;
    const mutationIntent = `scope:delete:${scope.id}`;
    try {
      await api.delete(`/channels/account-scopes/${scope.id}`, {
        params: { expected_version: scope.version },
        ...channelMutationConfig(mutationIntent),
      });
      completeMutationIntent(mutationIntent);
      messageRef.current.success("入口账号绑定已解除");
      loadData();
    } catch (err) {
      failMutationIntent(err, mutationIntent, "解除绑定失败");
    }
  };

  const openInvestigation = async (clue: DiversionClue) => {
    setCurrentClue(clue);
    setInvestigation(null);
    try {
      const { data } = await api.get(
        `/risk-dashboard/diversion-clues/${clue.id}/investigation`
      );
      setInvestigation(data);
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "调查记录加载失败"));
    }
  };

  const transitionClue = async (values: {
    resolution_action: DiversionClue["investigation_status"];
    resolution_note: string;
  }) => {
    if (!currentClue || !investigation || !access.canManage) return;
    const mutationIntent = `diversion:transition:${currentClue.id}:${investigation.version}`;
    try {
      await api.post(
        `/risk-dashboard/diversion-clues/${currentClue.id}/transition`,
        {
          expected_version: investigation.version,
          to_status: values.resolution_action,
          reason: values.resolution_note,
          resolution_note: values.resolution_note,
        },
        channelMutationConfig(mutationIntent)
      );
      completeMutationIntent(mutationIntent);
      messageRef.current.success("线索已处理");
      setCurrentClue(null);
      setInvestigation(null);
      resolveForm.resetFields();
      loadData();
    } catch (err) {
      failMutationIntent(err, mutationIntent, "处理失败");
    }
  };

  const reopenClue = async () => {
    if (!currentClue || !investigation || !access.canManage) return;
    const reason = "发现新信息，重新进入调查";
    const mutationIntent = `diversion:reopen:${currentClue.id}:${investigation.version}`;
    try {
      await api.post(
        `/risk-dashboard/diversion-clues/${currentClue.id}/transition`,
        { expected_version: investigation.version, to_status: "open", reason },
        channelMutationConfig(mutationIntent)
      );
      completeMutationIntent(mutationIntent);
      messageRef.current.success("线索已重开");
      await openInvestigation({
        ...currentClue,
        resolved: false,
        investigation_status: "open",
      });
      loadData();
    } catch (err) {
      failMutationIntent(err, mutationIntent, "重开失败");
    }
  };

  const addEvidence = async (values: {
    evidence_type: string;
    description: string;
  }) => {
    if (!currentClue || !investigation || !access.canManage) return;
    const mutationIntent = `diversion:evidence:${currentClue.id}:${investigation.version}`;
    try {
      await api.post(
        `/risk-dashboard/diversion-clues/${currentClue.id}/evidence`,
        {
          expected_version: investigation.version,
          evidence_type: values.evidence_type,
          description: values.description,
        },
        channelMutationConfig(mutationIntent)
      );
      completeMutationIntent(mutationIntent);
      evidenceForm.resetFields();
      messageRef.current.success("调查证据已保存");
      await openInvestigation(currentClue);
    } catch (err) {
      failMutationIntent(err, mutationIntent, "证据保存失败");
    }
  };

  const distributorColumns: ColumnsType<Distributor> = [
    {
      title: "经销商",
      dataIndex: "name",
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{record.name}</Text>
          <Text type="secondary">{record.code}</Text>
        </Space>
      ),
    },
    {
      title: "组织规模",
      render: (_, record) =>
        `${record.region_count} 个区域 / ${record.store_count} 个门店`,
    },
    {
      title: "已登记码量",
      render: (_, record) => `${record.allocated_quantity} 个码`,
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (s: string, record: Distributor) => (
        <Switch
          disabled={!access.canManage}
          checked={s === "active"}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            const mutationIntent = `distributor:status:${record.id}:${checked}`;
            try {
              await api.patch(
                `/channels/distributors/${record.id}`,
                {
                  status: checked ? "active" : "inactive",
                  expected_version: record.version,
                },
                channelMutationConfig(mutationIntent)
              );
              completeMutationIntent(mutationIntent);
              messageRef.current.success(checked ? "已启用" : "已停用");
              loadData();
            } catch (err) {
              failMutationIntent(err, mutationIntent, "操作失败");
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      render: (_, record) =>
        access.canManage ? (
          <Space>
            <Button
              size="small"
              type="link"
              icon={<EditOutlined />}
              onClick={() => openEntityModal("distributor", record)}
            >
              编辑
            </Button>
            <Button
              size="small"
              type="link"
              onClick={() =>
                openEntityModal("region", undefined, {
                  distributor_id: record.id,
                })
              }
            >
              创建区域
            </Button>
          </Space>
        ) : null,
    },
  ];

  const regionColumns: ColumnsType<Region> = [
    { title: "区域", dataIndex: "name" },
    {
      title: "覆盖范围",
      render: (_, record) => (
        <Space size={6}>
          <Tag>{coverageTypeLabel(record.coverage_type)}</Tag>
          <span>{regionCoverageLabel(record)}</span>
        </Space>
      ),
    },
    {
      title: "经销商",
      dataIndex: "distributor_name",
      render: (value) => value || "未绑定",
    },
    {
      title: "门店数",
      dataIndex: "store_count",
      render: (value) => `${value || 0} 个`,
    },
    {
      title: "已登记码量",
      dataIndex: "allocated_quantity",
      render: (value) => `${value || 0} 个码`,
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (s: string, record: Region) => (
        <Switch
          disabled={!access.canManage}
          checked={s === "active"}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            const mutationIntent = `region:status:${record.id}:${checked}`;
            try {
              await api.patch(
                `/channels/regions/${record.id}`,
                {
                  status: checked ? "active" : "inactive",
                  expected_version: record.version,
                },
                channelMutationConfig(mutationIntent)
              );
              completeMutationIntent(mutationIntent);
              messageRef.current.success(checked ? "已启用" : "已停用");
              loadData();
            } catch (err) {
              failMutationIntent(err, mutationIntent, "操作失败");
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      render: (_, record) =>
        access.canManage ? (
          <Space>
            <Button
              size="small"
              type="link"
              icon={<EditOutlined />}
              onClick={() => openEntityModal("region", record)}
            >
              编辑
            </Button>
          </Space>
        ) : null,
    },
  ];

  const storeColumns: ColumnsType<Store> = [
    { title: "门店", dataIndex: "name" },
    {
      title: "区域",
      dataIndex: "region_name",
      render: (value) => value || "未绑定",
    },
    {
      title: "经销商",
      dataIndex: "distributor_name",
      render: (value) => value || "未绑定",
    },
    {
      title: "已登记码量",
      dataIndex: "allocated_quantity",
      render: (value) => `${value || 0} 个码`,
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (s: string, record: Store) => (
        <Switch
          disabled={!access.canManage}
          checked={s === "active"}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            const mutationIntent = `store:status:${record.id}:${checked}`;
            try {
              await api.patch(
                `/channels/stores/${record.id}`,
                {
                  status: checked ? "active" : "inactive",
                  expected_version: record.version,
                },
                channelMutationConfig(mutationIntent)
              );
              completeMutationIntent(mutationIntent);
              messageRef.current.success(checked ? "已启用" : "已停用");
              loadData();
            } catch (err) {
              failMutationIntent(err, mutationIntent, "操作失败");
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      render: (_, record) =>
        access.canManage ? (
          <Space>
            <Button
              size="small"
              type="link"
              icon={<EditOutlined />}
              onClick={() => openEntityModal("store", record)}
            >
              编辑
            </Button>
          </Space>
        ) : null,
    },
  ];

  const allocationColumns: ColumnsType<Allocation> = [
    { title: "码批次", dataIndex: "batch_code" },
    {
      title: "产品/SKU",
      render: (_, record) =>
        [record.product_name || "未命名产品", record.sku_name]
          .filter(Boolean)
          .join(" / "),
    },
    {
      title: "流向范围",
      render: (_, record) =>
        record.store_name ||
        record.region_name ||
        record.distributor_name ||
        "未设置范围",
    },
    {
      title: "经销商",
      dataIndex: "distributor_name",
      render: (value) => value || "未绑定经销商",
    },
    {
      title: "登记数量",
      dataIndex: "quantity",
      render: (value) => `${value || 0} 个`,
    },
    {
      title: "版本/状态",
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          <Text>v{record.version}</Text>
          <Tag
            color={
              record.status === "active" ? STATUS_COLORS.success : undefined
            }
          >
            {record.effective_to
              ? "历史版本"
              : record.status === "active"
                ? "当前生效"
                : "已归档"}
          </Tag>
        </Space>
      ),
    },
    {
      title: "批次余量",
      dataIndex: "remaining_quantity",
      render: (value) => `剩余 ${value || 0}`,
    },
    {
      title: "操作",
      render: (_, record) =>
        access.canAllocate &&
        !record.effective_to &&
        record.status === "active" ? (
          <Space>
            <Button
              size="small"
              type="link"
              onClick={() => openAllocationReassign(record)}
            >
              重分配
            </Button>
            <Button
              size="small"
              type="link"
              danger
              onClick={() => setAllocationToArchive(record)}
            >
              归档
            </Button>
          </Space>
        ) : null,
    },
  ];

  const clueColumns: ColumnsType<DiversionClue> = [
    {
      title: "风险",
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          {severityTag(record.severity)}
          <Text type="secondary">{record.resolved ? "已处理" : "待处理"}</Text>
        </Space>
      ),
    },
    {
      title: "异常路径",
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{record.public_id}</Text>
          <Text>{`${record.expected_region || "未设置"} → ${record.detected_city || "未知"}`}</Text>
        </Space>
      ),
    },
    {
      title: "关联货品",
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          <Text>{productSkuLabel(record.product_name, record.sku_name)}</Text>
          <Text type="secondary">{record.batch_code || "未绑定批次"}</Text>
        </Space>
      ),
    },
    {
      title: "渠道归属",
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          <Text>
            {record.store_name ||
              record.region_name ||
              record.distributor_name ||
              "未识别渠道"}
          </Text>
          <Text type="secondary">
            {record.distributor_name || "未绑定经销商"}
          </Text>
        </Space>
      ),
    },
    { title: "扫码时间", dataIndex: "detected_at", render: formatDateTime },
    {
      title: "状态",
      dataIndex: "resolved",
      render: (value) =>
        value ? (
          <Tag color={STATUS_COLORS.success}>已处理</Tag>
        ) : (
          <Tag color={STATUS_COLORS.error}>待处理</Tag>
        ),
    },
    {
      title: "操作",
      render: (_, record) =>
        record.resolved ? (
          <Button
            size="small"
            type="link"
            onClick={() => void openInvestigation(record)}
          >
            查看记录
          </Button>
        ) : (
          <Button
            size="small"
            type="link"
            icon={<CheckOutlined />}
            onClick={() => void openInvestigation(record)}
          >
            查看处理
          </Button>
        ),
    },
  ];

  const scopeColumns: ColumnsType<AccountScope> = [
    {
      title: "账号",
      dataIndex: "account_id",
      render: (value) => {
        const account = accounts.find((item) => item.id === value);
        return account ? `${account.name} / ${account.email}` : value;
      },
    },
    {
      title: "入口类型",
      dataIndex: "scope_type",
      render: (value) =>
        value === "distributor"
          ? "经销商入口"
          : value === "region"
            ? "区域入口"
            : "门店入口",
    },
    {
      title: "绑定范围",
      render: (_, record) => {
        if (record.scope_type === "distributor") {
          return (
            distributors.items.find((item) => item.id === record.distributor_id)
              ?.name || record.distributor_id
          );
        }
        if (record.scope_type === "region") {
          return (
            regions.items.find((item) => item.id === record.region_id)?.name ||
            record.region_id
          );
        }
        return (
          stores.items.find((item) => item.id === record.store_id)?.name ||
          record.store_id
        );
      },
    },
    {
      title: "操作",
      render: (_, record) =>
        access.canScope ? (
          <Button
            size="small"
            type="link"
            danger
            onClick={() => deleteScope(record)}
          >
            解除绑定
          </Button>
        ) : null,
    },
  ];

  const entityTitle = entityModal
    ? entityModal.type === "distributor"
      ? "经销商"
      : entityModal.type === "region"
        ? "区域"
        : "门店"
    : "";

  return {
    appApi,
    messageRef,
    mutationKeysRef,
    autoRegionNameRef,
    overview,
    setOverview,
    distributors,
    setDistributors,
    regions,
    setRegions,
    stores,
    setStores,
    allocations,
    setAllocations,
    clues,
    setClues,
    accounts,
    setAccounts,
    scopes,
    setScopes,
    batches,
    setBatches,
    loading,
    setLoading,
    activeTab,
    setActiveTab,
    channelMutationConfig,
    completeMutationIntent,
    isConflictError,
    failMutationIntent,
    tenantFeatures,
    setTenantFeatures,
    clueResolvedFilter,
    setClueResolvedFilter,
    clueSeverityFilter,
    setClueSeverityFilter,
    entityModal,
    setEntityModal,
    entitySaving,
    setEntitySaving,
    createdDistributor,
    setCreatedDistributor,
    createdRegion,
    setCreatedRegion,
    allocationOpen,
    setAllocationOpen,
    allocationToReassign,
    setAllocationToReassign,
    allocationToArchive,
    setAllocationToArchive,
    scopeOpen,
    setScopeOpen,
    currentClue,
    setCurrentClue,
    investigation,
    setInvestigation,
    entityForm,
    allocationForm,
    archiveAllocationForm,
    scopeForm,
    resolveForm,
    evidenceForm,
    regionCoverageType,
    regionProvince,
    regionCity,
    regionCoverageProvinces,
    allocationBatchId,
    allocationTargetType,
    allocationRegionId,
    allocationDistributorId,
    allocationStoreId,
    allocationQuantity,
    scopeRegionId,
    loadData,
    selectedAllocationBatch,
    selectedBatchCapacity,
    allocationAvailableQuantity,
    distributorOptions,
    regionOptions,
    storeOptions,
    selectedAllocationRegion,
    selectedAllocationDistributor,
    selectedAllocationStore,
    allocationTargetLabel,
    allocationDistributorName,
    allocationSummary,
    openEntityModal,
    closeEntityModal,
    saveEntity,
    continueWithRegion,
    continueWithScope,
    continueWithAllocation,
    continueWithRegionScope,
    continueWithStore,
    createAllocation,
    openAllocationReassign,
    archiveAllocation,
    createScope,
    deleteScope,
    openInvestigation,
    transitionClue,
    reopenClue,
    addEvidence,
    distributorColumns,
    regionColumns,
    storeColumns,
    allocationColumns,
    clueColumns,
    scopeColumns,
    entityTitle,
  };
}

export type ChannelsWorkspaceApi = ReturnType<typeof useChannelsWorkspace>;
