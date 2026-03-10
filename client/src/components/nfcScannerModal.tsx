import { WifiOutlined } from "@ant-design/icons";
import { useTranslate } from "@refinedev/core";
import { Alert, Button, Descriptions, FloatButton, Modal, Segmented, Space, Spin, Typography } from "antd";
import React, { useCallback, useState } from "react";
import { useNavigate } from "react-router";
import { TigerTagData, isWebNfcSupported, useNfcCreateFromTag, useNfcRead, useNfcStatus } from "../utils/nfc";
import { decodeTigerTag, TIGERTAG_MAKER_V1 } from "../utils/tigertagCodec";

const { Text } = Typography;

/**
 * Renders decoded TigerTag data as a compact Descriptions panel.
 */
const TagDataSummary: React.FC<{ tagData: TigerTagData; t: (key: string) => string }> = ({ tagData, t }) => {
  const diameter =
    tagData.diameter_mm > 0
      ? `${tagData.diameter_mm} mm`
      : tagData.id_diameter === 1
        ? "1.75 mm"
        : tagData.id_diameter === 2
          ? "2.85 mm"
          : "—";

  return (
    <Descriptions column={2} size="small" bordered>
      <Descriptions.Item label={t("nfc.tag_color")}>
        {tagData.color_hex ? (
          <Space>
            <span
              style={{
                display: "inline-block",
                width: 16,
                height: 16,
                borderRadius: 3,
                backgroundColor: `#${tagData.color_hex}`,
                border: "1px solid #d9d9d9",
                verticalAlign: "middle",
              }}
            />
            #{tagData.color_hex}
          </Space>
        ) : (
          "—"
        )}
      </Descriptions.Item>
      <Descriptions.Item label={t("nfc.tag_diameter")}>{diameter}</Descriptions.Item>
      <Descriptions.Item label={t("nfc.tag_weight")}>
        {tagData.weight > 0 ? `${tagData.weight} g` : "—"}
      </Descriptions.Item>
      <Descriptions.Item label={t("nfc.tag_nozzle_temp")}>
        {tagData.nozzle_temp > 0 ? `${tagData.nozzle_temp} °C` : "—"}
      </Descriptions.Item>
      <Descriptions.Item label={t("nfc.tag_bed_temp")}>
        {tagData.bed_temp > 0 ? `${tagData.bed_temp} °C` : "—"}
      </Descriptions.Item>
    </Descriptions>
  );
};

const NfcScannerModal: React.FC = () => {
  const [visible, setVisible] = useState(false);
  const [mode, setMode] = useState<"browser" | "server">("server");
  const [browserScanning, setBrowserScanning] = useState(false);
  const [browserError, setBrowserError] = useState<string | null>(null);
  const [unmatchedTagData, setUnmatchedTagData] = useState<TigerTagData | null>(null);
  const t = useTranslate();
  const navigate = useNavigate();

  const nfcStatus = useNfcStatus();
  const nfcReadMutation = useNfcRead();
  const createFromTagMutation = useNfcCreateFromTag();

  const serverEnabled = nfcStatus.data?.enabled === true && nfcStatus.data?.status === "connected";
  const webNfcAvailable = isWebNfcSupported();

  const handleServerRead = useCallback(async () => {
    setUnmatchedTagData(null);
    const result = await nfcReadMutation.mutateAsync();
    if (result.success && result.spool_id) {
      setVisible(false);
      navigate(`/spool/show/${result.spool_id}`);
    } else if (result.success && result.tag_data && !result.spool_id) {
      setUnmatchedTagData(result.tag_data);
    }
  }, [nfcReadMutation, navigate]);

  const handleBrowserScan = useCallback(async () => {
    if (!window.NDEFReader) {
      setBrowserError(t("nfc.error.not_supported"));
      return;
    }

    setBrowserScanning(true);
    setBrowserError(null);
    setUnmatchedTagData(null);

    try {
      const reader = new window.NDEFReader();
      const controller = new AbortController();

      reader.onreading = (event: NDEFReadingEvent) => {
        controller.abort();
        setBrowserScanning(false);

        // Look through NDEF records for a TigerTag external type, Spoolman URI, or ID
        for (const record of event.message.records) {
          // Check for TigerTag NDEF external type record
          if (record.recordType === "tigertag.io:maker" && record.data) {
            try {
              const tagData = decodeTigerTag(record.data.buffer as ArrayBuffer);
              if (tagData.id_tigertag === TIGERTAG_MAKER_V1 && tagData.id_product > 0) {
                // Convert RGBA to hex string
                const colorHex = [tagData.color_r, tagData.color_g, tagData.color_b]
                  .map((c) => c.toString(16).padStart(2, "0"))
                  .join("");
                // Derive diameter from id_diameter
                const diameterMm = tagData.id_diameter === 1 ? 1.75 : tagData.id_diameter === 2 ? 2.85 : 0;

                setUnmatchedTagData({
                  id_tigertag: tagData.id_tigertag,
                  id_product: tagData.id_product,
                  id_material: tagData.id_material,
                  id_diameter: tagData.id_diameter,
                  id_brand: tagData.id_brand,
                  color_hex: colorHex,
                  weight: tagData.weight,
                  nozzle_temp: tagData.nozzle_temp,
                  bed_temp: tagData.bed_temp,
                  drying_temp: tagData.drying_temp,
                  drying_duration: tagData.drying_duration,
                  timestamp: tagData.timestamp,
                  user_message: tagData.user_message,
                  diameter_mm: diameterMm,
                });
                return;
              }
            } catch {
              // Failed to decode, fall through to other record types
            }
          }

          if (record.recordType === "url" || record.recordType === "text") {
            const decoder = new TextDecoder(record.encoding || "utf-8");
            const text = record.data ? decoder.decode(record.data) : "";

            // Check for spoolman:s-{id} format
            const spoolmanMatch = text.match(/web\+spoolman:s-(\d+)/);
            if (spoolmanMatch) {
              setVisible(false);
              navigate(`/spool/show/${spoolmanMatch[1]}`);
              return;
            }

            // Check for URL format
            const urlMatch = text.match(/\/spool\/show\/(\d+)/);
            if (urlMatch) {
              setVisible(false);
              navigate(`/spool/show/${urlMatch[1]}`);
              return;
            }
          }
        }

        setBrowserError(t("nfc.no_match"));
      };

      reader.onreadingerror = () => {
        controller.abort();
        setBrowserScanning(false);
        setBrowserError(t("nfc.error.read_failed"));
      };

      await reader.scan({ signal: controller.signal });
    } catch (error) {
      setBrowserScanning(false);
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        setBrowserError(t("nfc.error.permission_denied"));
      } else {
        setBrowserError(t("nfc.error.read_failed"));
      }
    }
  }, [t, navigate]);

  const handleCreateFromTag = useCallback(async () => {
    if (!unmatchedTagData) return;

    const result = await createFromTagMutation.mutateAsync({
      id_product: unmatchedTagData.id_product,
      id_material: unmatchedTagData.id_material,
      id_diameter: unmatchedTagData.id_diameter,
      id_brand: unmatchedTagData.id_brand,
      color_hex: unmatchedTagData.color_hex,
      weight: unmatchedTagData.weight,
      nozzle_temp: unmatchedTagData.nozzle_temp,
      bed_temp: unmatchedTagData.bed_temp,
      drying_temp: unmatchedTagData.drying_temp,
      drying_duration: unmatchedTagData.drying_duration,
      diameter_mm: unmatchedTagData.diameter_mm,
    });

    if (result.success && result.spool_id) {
      setVisible(false);
      setUnmatchedTagData(null);
      navigate(`/spool/show/${result.spool_id}`);
    }
  }, [unmatchedTagData, createFromTagMutation, navigate]);

  // Don't show the button if neither server NFC nor Web NFC is available
  if (!serverEnabled && !webNfcAvailable) {
    return null;
  }

  return (
    <>
      <FloatButton
        type="primary"
        onClick={() => setVisible(true)}
        icon={<WifiOutlined />}
        shape="circle"
        style={{ insetInlineEnd: 74 }}
      />
      <Modal
        open={visible}
        destroyOnClose
        onCancel={() => {
          setVisible(false);
          setBrowserScanning(false);
          setBrowserError(null);
          setUnmatchedTagData(null);
        }}
        footer={null}
        title={t("nfc.scan_title")}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Text>{t("nfc.scan_description")}</Text>

          <Segmented
            block
            options={[
              { label: t("nfc.mode_server"), value: "server", disabled: !serverEnabled },
              { label: t("nfc.mode_browser"), value: "browser", disabled: !webNfcAvailable },
            ]}
            value={mode}
            onChange={(value) => setMode(value as "browser" | "server")}
          />

          {mode === "server" && (
            <Space direction="vertical" style={{ width: "100%" }} align="center">
              <Button
                type="primary"
                onClick={handleServerRead}
                loading={nfcReadMutation.isPending}
                size="large"
              >
                {nfcReadMutation.isPending ? t("nfc.reading") : t("nfc.scan_title")}
              </Button>
              {nfcReadMutation.isSuccess && !nfcReadMutation.data?.spool_id && !unmatchedTagData && (
                <Alert
                  type="warning"
                  message={nfcReadMutation.data?.message || t("nfc.no_match")}
                  showIcon
                />
              )}
              {nfcReadMutation.isError && (
                <Alert type="error" message={t("nfc.error.read_failed")} showIcon />
              )}
            </Space>
          )}

          {mode === "browser" && (
            <Space direction="vertical" style={{ width: "100%" }} align="center">
              {browserScanning ? (
                <Spin tip={t("nfc.place_tag")}>
                  <div style={{ padding: 50 }} />
                </Spin>
              ) : (
                <Button type="primary" onClick={handleBrowserScan} size="large">
                  {t("nfc.scan_title")}
                </Button>
              )}
              {browserError && !unmatchedTagData && (
                <Alert type="error" message={browserError} showIcon />
              )}
            </Space>
          )}

          {/* Show tag data and create button when no spool matched */}
          {unmatchedTagData && (
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              <Alert type="info" message={t("nfc.create_from_tag_description")} showIcon />
              <TagDataSummary tagData={unmatchedTagData} t={t} />
              <Button
                type="primary"
                onClick={handleCreateFromTag}
                loading={createFromTagMutation.isPending}
                block
              >
                {createFromTagMutation.isPending ? t("nfc.creating_spool") : t("nfc.create_from_tag")}
              </Button>
              {createFromTagMutation.isSuccess && createFromTagMutation.data?.success && (
                <Alert type="success" message={t("nfc.create_success")} showIcon />
              )}
              {(createFromTagMutation.isError ||
                (createFromTagMutation.isSuccess && !createFromTagMutation.data?.success)) && (
                <Alert
                  type="error"
                  message={createFromTagMutation.data?.message || t("nfc.create_error")}
                  showIcon
                />
              )}
            </Space>
          )}
        </Space>
      </Modal>
    </>
  );
};

export default NfcScannerModal;
