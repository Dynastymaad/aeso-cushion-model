-- Day-ahead wind forecast vintages. Timestamp is when the forecast was made,
-- EffectiveDateTime the hour it is for. The API cannot reproduce this.
SELECT Timestamp, EffectiveDateTime, DataSourceName, Value
FROM WindForecast
WHERE MarketName = 'AESO'
  AND EffectiveDateTime >= DATEADD(day, -{DAYS}, GETDATE())
ORDER BY EffectiveDateTime ASC;
