import { LinkOutlined } from "@ant-design/icons";
import { useTranslate } from "@refinedev/core";
import { Alert, Button, Descriptions, Modal, Segmented, Space, Spin, Typography } from "antd";
import React, { useCallback, useState } from "react";
import { TigerTagData, isWebNfcSupported, useNfcBind, useNfcRead, useNfcStatus } from "../utils/nfc";
import { decodeTigerTag, TIGERTAG_MAKER_V1 } from "../utils/tigertagCodec";
import { ISpool } from "../pages/spools/model";

const { Text } = Typography;

interface NfcBindModalProps {
  spool?: ISpool;
  visible: boolean;
  onClose: () => void;
  onBound?: () => void;
}

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

const NfcBindModal: React.FC<NfcBindModalProps> = ({ spool, visible, onClose, onBound }) => {
  const [mode, setMode] = useState<"browser" | "server">("server");
  const [browserScanning, setBrowserScanning] = useState(false);
  const [browserError, setBrowserError] = useState<string | null>(null);
  const [scannedTagData, setScannedTagData] = useState<TigerTagData | null>(null);
  const [scannedRawB64, setScannedRawB64] = useState<string | null>(null);
  const t = useTranslate();

  const nfcStatus = useNfcStatus();
  const nfcReadMutation = useNfcRead();
  const bindMutation = useNfcBind();

  const serverEnabled = nfcStatus.data?.enabled === true && nfcStatus.data?.status === "connected";
  const webNfcAvailable = isWebNfcSupported();

  const resetState = useCallback(() => {
    setBrowserScanning(false);
    setBrowserError(null);
    setScannedTagData(null);
    setScannedRawB64(null);
    bindMutation.reset();
    nfcReadMutation.reset();
  }, [bindMutation, nfcReadMutation]);

  const handleClose = useCallback(() => {
    resetState();
    onClose();
  }, [resetState, onClose]);

  const handleServerRead = useCallback(async () => {
    setScannedTagData(null);
    setScannedRawB64(null);
    bindMutation.reset();

    const result = await nfcReadMutation.mutateAsync();
    if (result.success && result.tag_data) {
      setScannedTagData(result.tag_data);
      setScannedRawB64(result.raw_data_b64 || null);
    }
  }, [nfcReadMutation, bindMutation]);

  const handleBrowserScan = useCallback(async () => {
    if (!window.NDEFReader) {
      setBrowserError(t("nfc.error.not_supported"));
      return;
    }

    setBrowserScanning(true);
    setBrowserError(null);
    setScannedTagData(null);
    setScannedRawB64(null);
    bindMutation.reset();

    try {
      const reader = new window.NDEFReader();
      const controller = new AbortController();

      reader.onreading = (event: NDEFReadingEvent) => {
        controller.abort();
        setBrowserScanning(false);

        for (const record of event.message.records) {
          if (record.recordType === "tigertag.io:maker" && record.data) {
            try {
              const tagData = decodeTigerTag(record.data.buffer as ArrayBuffer);
              if (tagData.id_tigertag === TIGERTAG_MAKER_V1 && tagData.id_product > 0) {
                const colorHex = [tagData.color_r, tagData.color_g, tagData.color_b]
                  .map((c) => c.toString(16).padStart(2, "0"))
                  .join("");
                const diameterMm = tagData.id_diameter === 1 ? 1.75 : tagData.id_diameter === 2 ? 2.85 : 0;

                setScannedTagData({
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

                // Encode raw data as base64 for the bind request
                const bytes = new Uint8Array(record.data.buffer);
                const b64 = btoa(String.fromCharCode(...bytes));
                setScannedRawB64(b64);
                return;
              }
            } catch {
              // Fall through
            }
          }
        }

        setBrowserError(t("nfc.bind_no_tigertag"));
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
  }, [t, bindMutation]);

  const handleBind = useCallback(async () => {
    if (!spool || !scannedTagData) return;

    const request: { spool_id: number; raw_data_b64?: string; id_product?: number; timestamp?: number } = {
      spool_id: spool.id,
    };

    if (scannedRawB64) {
      request.raw_data_b64 = scannedRawB64;
    } else {
      // Browser scan decoded the tag — send id_product + timestamp
      request.id_product = scannedTagData.id_product;
      request.timestamp = scannedTagData.timestamp || 0;
    }

    await bindMutation.mutateAsync(request);
    if (onBound) {
      onBound();
    }
  }, [spool, scannedTagData, scannedRawB64, bindMutation, onBound]);

  return (
    <Modal
      open={visible}
      destroyOnClose
      onCancel={handleClose}
      footer={null}
      title={t("nfc.bind_title")}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Text>{t("nfc.bind_description")}</Text>

        <Segmented
          block
          options={[
            { label: t("nfc.mode_server"), value: "server", disabled: !serverEnabled },
            { label: t("nfc.mode_browser"), value: "browser", disabled: !webNfcAvailable },
          ]}
          value={mode}
          onChange={(value) => {
            setMode(value as "browser" | "server");
            resetState();
          }}
        />

        {/* Step 1: Scan */}
        {!scannedTagData && (
          <>
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
                {browserError && <Alert type="error" message={browserError} showIcon />}
              </Space>
            )}
          </>
        )}

        {/* Step 2: Confirm binding */}
        {scannedTagData && (
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <Alert type="info" message={t("nfc.bind_confirm_description")} showIcon />
            <TagDataSummary tagData={scannedTagData} t={t} />

            <Space style={{ width: "100%", justifyContent: "center" }}>
              <Button onClick={() => { setScannedTagData(null); setScannedRawB64(null); bindMutation.reset(); }}>
                {t("nfc.bind_scan_again")}
              </Button>
              <Button
                type="primary"
                icon={<LinkOutlined />}
                onClick={handleBind}
                loading={bindMutation.isPending}
              >
                {bindMutation.isPending ? t("nfc.bind_binding") : t("nfc.bind_button")}
              </Button>
            </Space>

            {bindMutation.isSuccess && bindMutation.data?.success && (
              <Alert type="success" message={bindMutation.data.message} showIcon />
            )}
            {bindMutation.isSuccess && !bindMutation.data?.success && (
              <Alert type="error" message={bindMutation.data?.message || t("nfc.bind_error")} showIcon />
            )}
            {bindMutation.isError && (
              <Alert type="error" message={t("nfc.bind_error")} showIcon />
            )}
          </Space>
        )}
      </Space>
    </Modal>
  );
};

export default NfcBindModal;
