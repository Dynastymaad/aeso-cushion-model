SELECT TimeStamp, EffectiveDateTime, DataSourceName, Value
FROM SolarForecast
WHERE MarketName = 'AESO'
  AND EffectiveDateTime >= DATEADD(day, -{DAYS}, GETDATE())
ORDER BY EffectiveDateTime ASC;
