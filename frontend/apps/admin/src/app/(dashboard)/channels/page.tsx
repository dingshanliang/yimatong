"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  CheckOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";

const { Title, Text } = Typography;

type PageResult<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};
type Overview = {
  distributor_count: number;
  region_count: number;
  store_count: number;
  allocated_quantity: number;
  pending_diversion_count: number;
};
type Distributor = {
  id: string;
  name: string;
  code: string;
  contact_name?: string;
  contact_phone_masked?: string;
  status: string;
  region_count: number;
  store_count: number;
  allocated_quantity: number;
};
type Region = {
  id: string;
  name: string;
  code: string;
  city?: string;
  province?: string;
  coverage_type?: "city" | "province" | "multi_province";
  coverage_areas?: { province?: string | null; city?: string | null }[];
  coverage_label?: string;
  status: string;
  distributor_id?: string;
  distributor_name?: string;
  store_count: number;
  allocated_quantity: number;
};
type Store = {
  id: string;
  name: string;
  code: string;
  address?: string;
  status: string;
  region_id?: string;
  region_name?: string;
  distributor_id?: string;
  distributor_name?: string;
  allocated_quantity: number;
};
type Allocation = {
  id: string;
  batch_id: string;
  batch_code?: string;
  product_name?: string;
  sku_name?: string;
  store_id?: string;
  store_name?: string;
  distributor_name?: string;
  distributor_id?: string;
  region_id?: string;
  region_name?: string;
  quantity: number;
  batch_quantity: number;
  remaining_quantity: number;
  allocated_at?: string;
};
type DiversionClue = {
  id: string;
  public_id: string;
  expected_region?: string;
  detected_city?: string;
  severity?: "high" | "medium" | "low";
  batch_code?: string;
  product_name?: string;
  sku_name?: string;
  distributor_name?: string;
  region_name?: string;
  store_name?: string;
  detected_at?: string | null;
  resolved: boolean;
  resolution_action?: string | null;
  resolution_note?: string | null;
  resolved_at?: string | null;
  handling_recommendation?: string;
};
type Account = { id: string; name: string; email: string };
type AccountScope = {
  id: string;
  account_id: string;
  scope_type: "distributor" | "region" | "store";
  distributor_id?: string;
  region_id?: string;
  store_id?: string;
};
type Batch = {
  id: string;
  batch_code: string;
  quantity: number;
  product_name?: string;
  sku_name?: string;
};

export const PROVINCE_CITY_OPTIONS = [
  { province: "北京", cities: ["北京"] },
  { province: "天津", cities: ["天津"] },
  {
    province: "河北",
    cities: [
      "石家庄",
      "唐山",
      "秦皇岛",
      "邯郸",
      "邢台",
      "保定",
      "张家口",
      "承德",
      "沧州",
      "廊坊",
      "衡水",
    ],
  },
  {
    province: "山西",
    cities: [
      "太原",
      "大同",
      "阳泉",
      "长治",
      "晋城",
      "朔州",
      "晋中",
      "运城",
      "忻州",
      "临汾",
      "吕梁",
    ],
  },
  {
    province: "内蒙古",
    cities: [
      "呼和浩特",
      "包头",
      "乌海",
      "赤峰",
      "通辽",
      "鄂尔多斯",
      "呼伦贝尔",
      "巴彦淖尔",
      "乌兰察布",
      "兴安盟",
      "锡林郭勒盟",
      "阿拉善盟",
    ],
  },
  {
    province: "辽宁",
    cities: [
      "沈阳",
      "大连",
      "鞍山",
      "抚顺",
      "本溪",
      "丹东",
      "锦州",
      "营口",
      "阜新",
      "辽阳",
      "盘锦",
      "铁岭",
      "朝阳",
      "葫芦岛",
    ],
  },
  {
    province: "吉林",
    cities: [
      "长春",
      "吉林",
      "四平",
      "辽源",
      "通化",
      "白山",
      "松原",
      "白城",
      "延边",
    ],
  },
  {
    province: "黑龙江",
    cities: [
      "哈尔滨",
      "齐齐哈尔",
      "鸡西",
      "鹤岗",
      "双鸭山",
      "大庆",
      "伊春",
      "佳木斯",
      "七台河",
      "牡丹江",
      "黑河",
      "绥化",
      "大兴安岭",
    ],
  },
  { province: "上海", cities: ["上海"] },
  {
    province: "江苏",
    cities: [
      "南京",
      "无锡",
      "徐州",
      "常州",
      "苏州",
      "南通",
      "连云港",
      "淮安",
      "盐城",
      "扬州",
      "镇江",
      "泰州",
      "宿迁",
    ],
  },
  {
    province: "浙江",
    cities: [
      "杭州",
      "宁波",
      "温州",
      "嘉兴",
      "湖州",
      "绍兴",
      "金华",
      "衢州",
      "舟山",
      "台州",
      "丽水",
    ],
  },
  {
    province: "安徽",
    cities: [
      "合肥",
      "芜湖",
      "蚌埠",
      "淮南",
      "马鞍山",
      "淮北",
      "铜陵",
      "安庆",
      "黄山",
      "滁州",
      "阜阳",
      "宿州",
      "六安",
      "亳州",
      "池州",
      "宣城",
    ],
  },
  {
    province: "福建",
    cities: [
      "福州",
      "厦门",
      "莆田",
      "三明",
      "泉州",
      "漳州",
      "南平",
      "龙岩",
      "宁德",
    ],
  },
  {
    province: "江西",
    cities: [
      "南昌",
      "景德镇",
      "萍乡",
      "九江",
      "新余",
      "鹰潭",
      "赣州",
      "吉安",
      "宜春",
      "抚州",
      "上饶",
    ],
  },
  {
    province: "山东",
    cities: [
      "济南",
      "青岛",
      "淄博",
      "枣庄",
      "东营",
      "烟台",
      "潍坊",
      "济宁",
      "泰安",
      "威海",
      "日照",
      "临沂",
      "德州",
      "聊城",
      "滨州",
      "菏泽",
    ],
  },
  {
    province: "河南",
    cities: [
      "郑州",
      "开封",
      "洛阳",
      "平顶山",
      "安阳",
      "鹤壁",
      "新乡",
      "焦作",
      "濮阳",
      "许昌",
      "漯河",
      "三门峡",
      "南阳",
      "商丘",
      "信阳",
      "周口",
      "驻马店",
      "济源",
    ],
  },
  {
    province: "湖北",
    cities: [
      "武汉",
      "黄石",
      "十堰",
      "宜昌",
      "襄阳",
      "鄂州",
      "荆门",
      "孝感",
      "荆州",
      "黄冈",
      "咸宁",
      "随州",
      "恩施",
      "仙桃",
      "潜江",
      "天门",
      "神农架",
    ],
  },
  {
    province: "湖南",
    cities: [
      "长沙",
      "株洲",
      "湘潭",
      "衡阳",
      "邵阳",
      "岳阳",
      "常德",
      "张家界",
      "益阳",
      "郴州",
      "永州",
      "怀化",
      "娄底",
      "湘西",
    ],
  },
  {
    province: "广东",
    cities: [
      "广州",
      "韶关",
      "深圳",
      "珠海",
      "汕头",
      "佛山",
      "江门",
      "湛江",
      "茂名",
      "肇庆",
      "惠州",
      "梅州",
      "汕尾",
      "河源",
      "阳江",
      "清远",
      "东莞",
      "中山",
      "潮州",
      "揭阳",
      "云浮",
    ],
  },
  {
    province: "广西",
    cities: [
      "南宁",
      "柳州",
      "桂林",
      "梧州",
      "北海",
      "防城港",
      "钦州",
      "贵港",
      "玉林",
      "百色",
      "贺州",
      "河池",
      "来宾",
      "崇左",
    ],
  },
  {
    province: "海南",
    cities: [
      "海口",
      "三亚",
      "三沙",
      "儋州",
      "五指山",
      "琼海",
      "文昌",
      "万宁",
      "东方",
      "定安",
      "屯昌",
      "澄迈",
      "临高",
      "白沙",
      "昌江",
      "乐东",
      "陵水",
      "保亭",
      "琼中",
    ],
  },
  { province: "重庆", cities: ["重庆"] },
  {
    province: "四川",
    cities: [
      "成都",
      "自贡",
      "攀枝花",
      "泸州",
      "德阳",
      "绵阳",
      "广元",
      "遂宁",
      "内江",
      "乐山",
      "南充",
      "眉山",
      "宜宾",
      "广安",
      "达州",
      "雅安",
      "巴中",
      "资阳",
      "阿坝",
      "甘孜",
      "凉山",
    ],
  },
  {
    province: "贵州",
    cities: [
      "贵阳",
      "六盘水",
      "遵义",
      "安顺",
      "毕节",
      "铜仁",
      "黔西南",
      "黔东南",
      "黔南",
    ],
  },
  {
    province: "云南",
    cities: [
      "昆明",
      "曲靖",
      "玉溪",
      "保山",
      "昭通",
      "丽江",
      "普洱",
      "临沧",
      "楚雄",
      "红河",
      "文山",
      "西双版纳",
      "大理",
      "德宏",
      "怒江",
      "迪庆",
    ],
  },
  {
    province: "西藏",
    cities: ["拉萨", "日喀则", "昌都", "林芝", "山南", "那曲", "阿里"],
  },
  {
    province: "陕西",
    cities: [
      "西安",
      "铜川",
      "宝鸡",
      "咸阳",
      "渭南",
      "延安",
      "汉中",
      "榆林",
      "安康",
      "商洛",
    ],
  },
  {
    province: "甘肃",
    cities: [
      "兰州",
      "嘉峪关",
      "金昌",
      "白银",
      "天水",
      "武威",
      "张掖",
      "平凉",
      "酒泉",
      "庆阳",
      "定西",
      "陇南",
      "临夏",
      "甘南",
    ],
  },
  {
    province: "青海",
    cities: ["西宁", "海东", "海北", "黄南", "海南", "果洛", "玉树", "海西"],
  },
  { province: "宁夏", cities: ["银川", "石嘴山", "吴忠", "固原", "中卫"] },
  {
    province: "新疆",
    cities: [
      "乌鲁木齐",
      "克拉玛依",
      "吐鲁番",
      "哈密",
      "昌吉",
      "博尔塔拉",
      "巴音郭楞",
      "阿克苏",
      "克孜勒苏",
      "喀什",
      "和田",
      "伊犁",
      "塔城",
      "阿勒泰",
      "石河子",
      "阿拉尔",
      "图木舒克",
      "五家渠",
      "北屯",
      "铁门关",
      "双河",
      "可克达拉",
      "昆玉",
      "胡杨河",
      "新星",
      "白杨",
    ],
  },
  { province: "香港", cities: ["香港"] },
  { province: "澳门", cities: ["澳门"] },
  {
    province: "台湾",
    cities: [
      "台北",
      "新北",
      "桃园",
      "台中",
      "台南",
      "高雄",
      "基隆",
      "新竹",
      "嘉义",
    ],
  },
];

const emptyPage = <T,>(): PageResult<T> => ({
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
});

function statusTag(status?: string) {
  if (status === "active") return <Tag color="#16a34a">启用</Tag>;
  if (status === "inactive") return <Tag color="#8c8c8c">停用</Tag>;
  return <Tag>{status || "未知"}</Tag>;
}

function productSkuLabel(productName?: string, skuName?: string) {
  return [productName || "未命名产品", skuName].filter(Boolean).join(" / ");
}

function severityTag(severity?: DiversionClue["severity"]) {
  if (severity === "high") return <Tag color="#b91c1c">高风险</Tag>;
  if (severity === "medium") return <Tag color="#f59e0b">中风险</Tag>;
  return <Tag>低风险</Tag>;
}

function resolutionActionLabel(action?: string | null) {
  if (action === "confirmed_diversion") return "确认窜货";
  if (action === "false_positive") return "误报";
  if (action === "contacted_channel") return "已联系渠道";
  if (action === "follow_up") return "继续跟进";
  return "未填写";
}

function formatDateTime(value?: string | null) {
  if (!value) return "未记录";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function batchCapacity(batch: Batch | undefined, allocations: Allocation[]) {
  if (!batch) return { total: 0, allocated: 0, remaining: 0 };
  const related = allocations.filter(
    (item) => item.batch_id === batch.id || item.batch_code === batch.batch_code
  );
  const latestRemaining = related.find(
    (item) => typeof item.remaining_quantity === "number"
  )?.remaining_quantity;
  const total =
    related.find((item) => item.batch_quantity)?.batch_quantity ||
    batch.quantity ||
    0;
  const allocated = related.reduce(
    (sum, item) => sum + (item.quantity || 0),
    0
  );
  const remaining =
    typeof latestRemaining === "number"
      ? latestRemaining
      : Math.max(total - allocated, 0);
  return {
    total,
    allocated: Math.max(total - remaining, allocated),
    remaining,
  };
}

function regionCoverageLabel(region: Region) {
  return region.coverage_label || region.city || region.province || "未设置";
}

function coverageTypeLabel(type?: Region["coverage_type"]) {
  if (type === "province") return "省级片区";
  if (type === "multi_province") return "大区片区";
  return "城市片区";
}

function buildRegionInitialValues(
  record?: Distributor | Region | Store,
  defaults?: Record<string, unknown>
) {
  const values = { ...(record || {}), ...(defaults || {}) } as Record<
    string,
    unknown
  >;
  if (record && "coverage_type" in record) {
    const region = record as Region;
    values.coverage_type = region.coverage_type || "city";
    if (region.coverage_type === "multi_province") {
      values.coverage_provinces = (region.coverage_areas || [])
        .map((area) => area.province)
        .filter(Boolean);
    }
  } else if (!values.coverage_type) {
    values.coverage_type = "city";
  }
  return values;
}

function buildRegionPayload(values: Record<string, unknown>) {
  const coverageType = (values.coverage_type ||
    "city") as Region["coverage_type"];
  const payload = { ...values };
  delete payload.coverage_provinces;

  if (coverageType === "province") {
    payload.city = null;
    payload.coverage_areas = [{ province: values.province, city: null }];
  } else if (coverageType === "multi_province") {
    const provinces = Array.isArray(values.coverage_provinces)
      ? (values.coverage_provinces as string[])
      : [];
    payload.province = provinces[0];
    payload.city = null;
    payload.coverage_areas = provinces.map((province) => ({
      province,
      city: null,
    }));
  } else {
    payload.coverage_areas = [{ province: values.province, city: values.city }];
  }

  return payload;
}

export default function ChannelsPage() {
  const appApi = App.useApp();
  const messageRef = useRef(appApi.message);
  const modalRef = useRef(appApi.modal);
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
  const [scopeOpen, setScopeOpen] = useState(false);
  const [currentClue, setCurrentClue] = useState<DiversionClue | null>(null);
  const [entityForm] = Form.useForm();
  const [allocationForm] = Form.useForm();
  const [scopeForm] = Form.useForm();
  const [resolveForm] = Form.useForm();
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
    modalRef.current = appApi.modal;
  }, [appApi.message, appApi.modal]);

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
        api.get("/channels/code-allocations"),
        api.get(clueUrl),
        api.get("/code-batches"),
        api.get("/accounts"),
        api.get("/channels/account-scopes"),
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
      setScopes(Array.isArray(scopeRes.data) ? scopeRes.data : []);
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "加载渠道数据失败"));
    } finally {
      setLoading(false);
    }
  }, [clueResolvedFilter, clueSeverityFilter]);

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
    if (!entityModal) return;
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
          payload
        );
        messageRef.current.success("资料已更新");
        closeEntityModal();
      } else {
        const { data } = await api.post(paths[entityModal.type], payload);
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
      loadData();
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "保存失败"));
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
    try {
      await api.post("/channels/code-allocations", values);
      messageRef.current.success("流向已登记");
      setAllocationOpen(false);
      allocationForm.resetFields();
      loadData();
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "分配失败"));
    }
  };

  const createScope = async (values: Record<string, unknown>) => {
    try {
      await api.post("/channels/account-scopes", values);
      messageRef.current.success("账号范围已绑定");
      setScopeOpen(false);
      scopeForm.resetFields();
      loadData();
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "绑定失败"));
    }
  };

  const resolveClue = async (values: {
    resolution_action?: string;
    resolution_note?: string;
  }) => {
    if (!currentClue) return;
    try {
      await api.put(
        `/risk-dashboard/diversion-clues/${currentClue.id}/resolve`,
        {
          resolution_action: values.resolution_action || "contacted_channel",
          resolution_note: values.resolution_note || "",
        }
      );
      messageRef.current.success("线索已处理");
      setCurrentClue(null);
      resolveForm.resetFields();
      loadData();
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "处理失败"));
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
          checked={s === "active"}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            try {
              await api.patch(`/channels/distributors/${record.id}`, {
                status: checked ? "active" : "inactive",
              });
              messageRef.current.success(checked ? "已启用" : "已停用");
              loadData();
            } catch (err) {
              messageRef.current.error(extractErrorMessage(err, "操作失败"));
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      render: (_, record) => (
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
      ),
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
          checked={s === "active"}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            try {
              await api.patch(`/channels/regions/${record.id}`, {
                status: checked ? "active" : "inactive",
              });
              messageRef.current.success(checked ? "已启用" : "已停用");
              loadData();
            } catch (err) {
              messageRef.current.error(extractErrorMessage(err, "操作失败"));
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      render: (_, record) => (
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
      ),
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
          checked={s === "active"}
          checkedChildren="启用"
          unCheckedChildren="停用"
          onChange={async (checked) => {
            try {
              await api.patch(`/channels/stores/${record.id}`, {
                status: checked ? "active" : "inactive",
              });
              messageRef.current.success(checked ? "已启用" : "已停用");
              loadData();
            } catch (err) {
              messageRef.current.error(extractErrorMessage(err, "操作失败"));
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      render: (_, record) => (
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
      ),
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
      title: "批次余量",
      dataIndex: "remaining_quantity",
      render: (value) => `剩余 ${value || 0}`,
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
          <Tag color="#16a34a">已处理</Tag>
        ) : (
          <Tag color="#b91c1c">待处理</Tag>
        ),
    },
    {
      title: "操作",
      render: (_, record) =>
        record.resolved ? (
          <Button
            size="small"
            type="link"
            onClick={() => setCurrentClue(record)}
          >
            查看记录
          </Button>
        ) : (
          <Button
            size="small"
            type="link"
            icon={<CheckOutlined />}
            onClick={() => setCurrentClue(record)}
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
  ];

  const entityTitle = entityModal
    ? entityModal.type === "distributor"
      ? "经销商"
      : entityModal.type === "region"
        ? "区域"
        : "门店"
    : "";

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <Title level={4} className="!mb-1">
            渠道管理
          </Title>
          <Text type="secondary">
            维护渠道组织，登记已赋码货品流向，并跟进跨区扫码线索。
          </Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={loadData}>
          刷新
        </Button>
      </div>

      <Row gutter={[16, 16]} className="mb-5">
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="经销商"
              value={overview?.distributor_count || 0}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="区域" value={overview?.region_count || 0} />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="门店" value={overview?.store_count || 0} />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="已登记码量"
              value={overview?.allocated_quantity || 0}
            />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic
              title="待处理线索"
              value={overview?.pending_diversion_count || 0}
            />
          </Card>
        </Col>
      </Row>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        onTabClick={setActiveTab}
        items={[
          {
            key: "distributors",
            label: "经销商",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => openEntityModal("distributor")}
                  >
                    新建经销商
                  </Button>
                </div>
                <Table
                  columns={distributorColumns}
                  dataSource={distributors.items}
                  rowKey="id"
                  loading={loading}
                  locale={{
                    emptyText: (
                      <Empty description="先新建经销商，再绑定区域和门店" />
                    ),
                  }}
                />
              </>
            ),
          },
          {
            key: "regions",
            label: "区域",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => openEntityModal("region")}
                  >
                    新建区域
                  </Button>
                </div>
                <Table
                  columns={regionColumns}
                  dataSource={regions.items}
                  rowKey="id"
                  loading={loading}
                />
              </>
            ),
          },
          {
            key: "stores",
            label: "门店",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => openEntityModal("store")}
                  >
                    新建门店
                  </Button>
                </div>
                <Table
                  columns={storeColumns}
                  dataSource={stores.items}
                  rowKey="id"
                  loading={loading}
                />
              </>
            ),
          },
          {
            key: "scopes",
            label: "账号授权",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button
                    type="primary"
                    icon={<SafetyCertificateOutlined />}
                    onClick={() => setScopeOpen(true)}
                  >
                    绑定入口账号
                  </Button>
                </div>
                <Table
                  columns={scopeColumns}
                  dataSource={scopes}
                  rowKey="id"
                  loading={loading}
                />
              </>
            ),
          },
          {
            key: "assign",
            label: "流向登记",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => setAllocationOpen(true)}
                  >
                    新建流向
                  </Button>
                </div>
                <Table
                  columns={allocationColumns}
                  dataSource={allocations.items}
                  rowKey="id"
                  loading={loading}
                />
              </>
            ),
          },
          {
            key: "diversion",
            label: "窜货线索",
            children: (
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
            ),
          },
        ].filter(
          (item) => item.key !== "stores" || tenantFeatures.channel_store
        )}
      />

      <Modal
        title={
          entityModal
            ? `${entityModal.record ? "编辑" : "新建"}${entityTitle}`
            : undefined
        }
        open={!!entityModal}
        onCancel={closeEntityModal}
        onOk={() => entityForm.submit()}
        okText={
          entityModal?.record
            ? "保存资料"
            : entityModal?.type === "distributor"
              ? "创建经销商"
              : entityModal?.type === "region"
                ? "创建区域"
                : "创建门店"
        }
        cancelText="取消"
        confirmLoading={entitySaving}
        footer={createdDistributor || createdRegion ? null : undefined}
        forceRender
        destroyOnHidden
      >
        {createdDistributor ? (
          <Space orientation="vertical" size={16} className="w-full">
            <Alert
              type="success"
              showIcon
              title={`已创建经销商：${createdDistributor.name}`}
              description={`系统编码：${createdDistributor.code}`}
            />
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="系统编码">
                {createdDistributor.code}
              </Descriptions.Item>
              <Descriptions.Item label="联系人">
                {createdDistributor.contact_name || "未填写"}
              </Descriptions.Item>
              <Descriptions.Item label="联系电话">
                {createdDistributor.contact_phone_masked || "未填写"}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {createdDistributor.status === "inactive" ? "停用" : "启用"}
              </Descriptions.Item>
            </Descriptions>
            <Space wrap>
              <Button type="primary" onClick={continueWithRegion}>
                创建区域
              </Button>
              <Button onClick={continueWithScope}>绑定入口账号</Button>
              <Button onClick={continueWithAllocation}>登记流向</Button>
              <Button onClick={closeEntityModal}>完成</Button>
            </Space>
          </Space>
        ) : createdRegion ? (
          <Space orientation="vertical" size={16} className="w-full">
            <Alert
              type="success"
              showIcon
              title={`已创建区域：${createdRegion.name}`}
              description={`系统编码：${createdRegion.code}`}
            />
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="系统编码">
                {createdRegion.code}
              </Descriptions.Item>
              <Descriptions.Item label="省市">
                {[createdRegion.province, createdRegion.city]
                  .filter(Boolean)
                  .join(" / ") || "未设置"}
              </Descriptions.Item>
              <Descriptions.Item label="所属经销商">
                {createdRegion.distributor_name || "已绑定经销商"}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {createdRegion.status === "inactive" ? "停用" : "启用"}
              </Descriptions.Item>
            </Descriptions>
            <Space wrap>
              <Button type="primary" onClick={continueWithAllocation}>
                登记流向
              </Button>
              <Button onClick={continueWithRegionScope}>绑定入口账号</Button>
              <Button onClick={closeEntityModal}>查看区域统计</Button>
              <Button onClick={continueWithStore}>可选创建门店</Button>
              <Button onClick={closeEntityModal}>完成</Button>
            </Space>
          </Space>
        ) : (
          <Form
            form={entityForm}
            layout="vertical"
            onFinish={saveEntity}
            initialValues={{ status: "active" }}
          >
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, message: "请输入名称" }]}
            >
              <Input
                placeholder={
                  entityModal?.type === "distributor"
                    ? "如华东经销商"
                    : "请输入名称"
                }
              />
            </Form.Item>
            {entityModal?.record && (
              <Form.Item name="code" label="编码">
                <Input disabled />
              </Form.Item>
            )}
            {!entityModal?.record && (
              <Alert
                className="mb-4"
                type="info"
                showIcon
                title="编码将在创建后自动生成"
              />
            )}
            {entityModal?.type === "distributor" && (
              <>
                <Form.Item name="contact_name" label="联系人">
                  <Input placeholder="负责日常对接的联系人" />
                </Form.Item>
                <Form.Item
                  name="contact_phone"
                  label="联系电话"
                  rules={[
                    { pattern: /^1\d{10}$/, message: "请输入 11 位手机号" },
                  ]}
                >
                  <Input placeholder="用于渠道协同联系" />
                </Form.Item>
              </>
            )}
            {entityModal?.type === "region" && (
              <>
                <Form.Item
                  name="coverage_type"
                  label="覆盖类型"
                  rules={[{ required: true, message: "请选择覆盖类型" }]}
                >
                  <Select
                    options={[
                      { label: "城市片区", value: "city" },
                      { label: "省级片区", value: "province" },
                      { label: "大区片区", value: "multi_province" },
                    ]}
                    onChange={() => {
                      entityForm.setFieldsValue({
                        province: undefined,
                        city: undefined,
                        coverage_provinces: undefined,
                      });
                    }}
                  />
                </Form.Item>
                {(regionCoverageType || "city") !== "multi_province" && (
                  <Form.Item
                    name="province"
                    label="省份"
                    rules={[{ required: true, message: "请选择省份" }]}
                  >
                    <Select
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择省份"
                      options={PROVINCE_CITY_OPTIONS.map((item) => ({
                        label: item.province,
                        value: item.province,
                      }))}
                      onChange={() =>
                        entityForm.setFieldValue("city", undefined)
                      }
                    />
                  </Form.Item>
                )}
                {(regionCoverageType || "city") === "city" && (
                  <Form.Item
                    name="city"
                    label="城市"
                    rules={[{ required: true, message: "请选择城市" }]}
                  >
                    <Select
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择城市"
                      options={(
                        PROVINCE_CITY_OPTIONS.find(
                          (item) => item.province === regionProvince
                        )?.cities || []
                      ).map((city) => ({
                        label: city,
                        value: city,
                      }))}
                      disabled={!regionProvince}
                    />
                  </Form.Item>
                )}
                {regionCoverageType === "multi_province" && (
                  <Form.Item
                    name="coverage_provinces"
                    label="覆盖省份"
                    rules={[
                      {
                        required: true,
                        type: "array",
                        min: 2,
                        message: "请至少选择两个省份",
                      },
                    ]}
                  >
                    <Select
                      mode="multiple"
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择大区覆盖的省份"
                      options={PROVINCE_CITY_OPTIONS.map((item) => ({
                        label: item.province,
                        value: item.province,
                      }))}
                    />
                  </Form.Item>
                )}
                <Form.Item name="distributor_id" label="所属经销商">
                  <Select allowClear options={distributorOptions} />
                </Form.Item>
              </>
            )}
            {entityModal?.type === "store" && (
              <>
                <Form.Item name="region_id" label="所属区域">
                  <Select
                    allowClear
                    options={regionOptions}
                    onChange={(regionId) => {
                      const region = regions.items.find(
                        (item) => item.id === regionId
                      );
                      if (region?.distributor_id) {
                        entityForm.setFieldValue(
                          "distributor_id",
                          region.distributor_id
                        );
                      }
                    }}
                  />
                </Form.Item>
                <Form.Item name="distributor_id" label="所属经销商">
                  <Select allowClear options={distributorOptions} />
                </Form.Item>
                <Form.Item name="address" label="地址">
                  <Input />
                </Form.Item>
              </>
            )}
          </Form>
        )}
      </Modal>

      <Modal
        title="新建流向登记"
        open={allocationOpen}
        onCancel={() => setAllocationOpen(false)}
        onOk={() => allocationForm.submit()}
        okText="确认登记"
        cancelText="取消"
        styles={{
          body: { maxHeight: "calc(100vh - 220px)", overflowY: "auto" },
        }}
        forceRender
        destroyOnHidden
      >
        <Form
          form={allocationForm}
          layout="vertical"
          onFinish={createAllocation}
          initialValues={{ target_type: "region" }}
        >
          <Alert
            className="mb-4"
            type="info"
            showIcon
            title="记录这批已赋码货品发往哪个渠道，不开放打印或下载码包。"
          />
          <Form.Item
            name="batch_id"
            label="码批次"
            rules={[{ required: true, message: "请选择码批次" }]}
          >
            <Select
              options={batches.map((batch) => ({
                label: batch.batch_code,
                value: batch.id,
              }))}
              onChange={() => allocationForm.setFieldValue("quantity", 1)}
            />
          </Form.Item>
          {selectedAllocationBatch && (
            <Card size="small" className="mb-4">
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="批次号">
                  {selectedAllocationBatch.batch_code}
                </Descriptions.Item>
                <Descriptions.Item label="产品/SKU">
                  {productSkuLabel(
                    selectedAllocationBatch.product_name,
                    selectedAllocationBatch.sku_name
                  )}
                </Descriptions.Item>
              </Descriptions>
              <Space className="mt-2" wrap>
                <Tag>总量 {selectedBatchCapacity.total}</Tag>
                <Tag color="#1d4ed8">
                  已登记 {selectedBatchCapacity.allocated}
                </Tag>
                <Tag color="#16a34a">
                  剩余 {selectedBatchCapacity.remaining}
                </Tag>
              </Space>
            </Card>
          )}
          <Form.Item
            name="target_type"
            label="流向层级"
            rules={[{ required: true, message: "请选择流向层级" }]}
          >
            <Select
              options={[
                { label: "经销商", value: "distributor" },
                { label: "区域", value: "region" },
                { label: "门店", value: "store" },
              ]}
              onChange={(value) => {
                allocationForm.setFieldsValue({
                  distributor_id:
                    value === "distributor"
                      ? allocationForm.getFieldValue("distributor_id")
                      : undefined,
                  region_id:
                    value === "region" ? regions.items[0]?.id : undefined,
                  store_id: undefined,
                });
              }}
            />
          </Form.Item>
          {allocationTargetType === "distributor" && (
            <Form.Item
              name="distributor_id"
              label="选择经销商"
              rules={[{ required: true, message: "请选择经销商" }]}
            >
              <Select options={distributorOptions} />
            </Form.Item>
          )}
          {allocationTargetType === "region" && (
            <>
              <Form.Item
                name="region_id"
                label="选择区域"
                rules={[{ required: true, message: "请选择区域" }]}
              >
                <Select options={regionOptions} />
              </Form.Item>
              <Form.Item name="distributor_id" hidden>
                <Input />
              </Form.Item>
              {allocationDistributorName && (
                <Alert
                  className="mb-4"
                  type="info"
                  showIcon
                  title={`系统已根据区域带出经销商：${allocationDistributorName}`}
                  description="登记到区域后，经销商入口和区域入口可查看这批货品流向。"
                />
              )}
            </>
          )}
          {allocationTargetType === "store" && (
            <>
              <Form.Item
                name="store_id"
                label="选择门店"
                rules={[{ required: true, message: "请选择门店" }]}
              >
                <Select
                  options={storeOptions}
                  onChange={(storeId) => {
                    const store = stores.items.find(
                      (item) => item.id === storeId
                    );
                    allocationForm.setFieldsValue({
                      distributor_id: store?.distributor_id,
                      region_id: store?.region_id,
                    });
                  }}
                />
              </Form.Item>
              <Alert
                className="mb-4"
                type="info"
                showIcon
                title="门店入口可查看本次收货批次和扫码趋势，不开放打印或下载码包。"
              />
            </>
          )}
          <Form.Item label="登记数量" required>
            <Space.Compact className="w-full">
              <Form.Item
                name="quantity"
                noStyle
                rules={[{ required: true, message: "请输入登记数量" }]}
              >
                <InputNumber
                  aria-label="登记数量"
                  min={1}
                  max={selectedBatchCapacity.remaining || undefined}
                  className="w-full"
                />
              </Form.Item>
              <Button
                onClick={() =>
                  allocationForm.setFieldValue(
                    "quantity",
                    selectedBatchCapacity.remaining
                  )
                }
                disabled={!selectedBatchCapacity.remaining}
              >
                全部登记
              </Button>
            </Space.Compact>
          </Form.Item>
          <Text type="secondary">
            可登记 1-{selectedBatchCapacity.remaining || 0} 个
          </Text>
          {allocationSummary && (
            <Alert
              className="mt-4"
              type="success"
              showIcon
              title={allocationSummary}
            />
          )}
        </Form>
      </Modal>

      <Modal
        title="绑定入口账号"
        open={scopeOpen}
        onCancel={() => setScopeOpen(false)}
        onOk={() => scopeForm.submit()}
        forceRender
        destroyOnHidden
      >
        <Form form={scopeForm} layout="vertical" onFinish={createScope}>
          <Form.Item
            name="account_id"
            label="账号"
            rules={[{ required: true, message: "请选择账号" }]}
          >
            <Select
              options={accounts.map((account) => ({
                label: `${account.name} / ${account.email}`,
                value: account.id,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="scope_type"
            label="入口类型"
            rules={[{ required: true, message: "请选择入口类型" }]}
          >
            <Select
              options={[
                { label: "经销商入口", value: "distributor" },
                { label: "区域入口", value: "region" },
                { label: "门店入口", value: "store" },
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) =>
              getFieldValue("scope_type") === "store" ? (
                <Form.Item
                  name="store_id"
                  label="门店"
                  rules={[{ required: true, message: "请选择门店" }]}
                >
                  <Select options={storeOptions} />
                </Form.Item>
              ) : getFieldValue("scope_type") === "region" ? (
                <>
                  <Form.Item
                    name="region_id"
                    label="区域"
                    rules={[{ required: true, message: "请选择区域" }]}
                  >
                    <Select options={regionOptions} />
                  </Form.Item>
                  <Form.Item name="distributor_id" label="所属经销商">
                    <Select disabled options={distributorOptions} />
                  </Form.Item>
                </>
              ) : (
                <Form.Item
                  name="distributor_id"
                  label="经销商"
                  rules={[{ required: true, message: "请选择经销商" }]}
                >
                  <Select options={distributorOptions} />
                </Form.Item>
              )
            }
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title="窜货线索处理"
        open={!!currentClue}
        onClose={() => setCurrentClue(null)}
        size="large"
      >
        {currentClue && (
          <Space orientation="vertical" className="w-full" size={16}>
            <Alert
              type={currentClue.resolved ? "success" : "warning"}
              showIcon
              title={
                <Space wrap>
                  {severityTag(currentClue.severity)}
                  <Text strong>异常路径</Text>
                  <Text>{`${currentClue.expected_region || "未设置"} → ${currentClue.detected_city || "未知"}`}</Text>
                </Space>
              }
            />
            <Card title="关联货品" size="small">
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="码">
                  {currentClue.public_id}
                </Descriptions.Item>
                <Descriptions.Item label="产品/SKU">
                  {productSkuLabel(
                    currentClue.product_name,
                    currentClue.sku_name
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="码批次">
                  {currentClue.batch_code || "未绑定批次"}
                </Descriptions.Item>
                <Descriptions.Item label="扫码时间">
                  {formatDateTime(currentClue.detected_at)}
                </Descriptions.Item>
              </Descriptions>
            </Card>
            <Card title="渠道归属" size="small">
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="经销商">
                  {currentClue.distributor_name || "未绑定"}
                </Descriptions.Item>
                <Descriptions.Item label="区域">
                  {currentClue.region_name ||
                    currentClue.expected_region ||
                    "未设置"}
                </Descriptions.Item>
                <Descriptions.Item label="门店">
                  {currentClue.store_name || "未识别门店"}
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  {currentClue.resolved ? "已处理" : "待处理"}
                </Descriptions.Item>
                {currentClue.resolved && (
                  <>
                    <Descriptions.Item label="处理结果">
                      {resolutionActionLabel(currentClue.resolution_action)}
                    </Descriptions.Item>
                    <Descriptions.Item label="处理时间">
                      {formatDateTime(currentClue.resolved_at)}
                    </Descriptions.Item>
                  </>
                )}
              </Descriptions>
            </Card>
            <Alert
              type="info"
              showIcon
              title="建议动作"
              description={
                currentClue.handling_recommendation ||
                "联系渠道核实货物流向，记录处理结果。"
              }
            />
            <Form form={resolveForm} layout="vertical" onFinish={resolveClue}>
              <Form.Item
                name="resolution_action"
                label="处理结果"
                rules={[{ required: true, message: "请选择处理结果" }]}
              >
                <Select
                  disabled={currentClue.resolved}
                  options={[
                    { label: "确认窜货", value: "confirmed_diversion" },
                    { label: "误报", value: "false_positive" },
                    { label: "已联系渠道", value: "contacted_channel" },
                    { label: "继续跟进", value: "follow_up" },
                  ]}
                />
              </Form.Item>
              <Form.Item name="resolution_note" label="处理记录">
                <Input.TextArea
                  rows={4}
                  placeholder="填写处理记录"
                  disabled={currentClue.resolved}
                />
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                icon={<CheckOutlined />}
                disabled={currentClue.resolved}
              >
                标记为已处理
              </Button>
            </Form>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
