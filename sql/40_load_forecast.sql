SELECT Timestamp, EffectiveDateTime, DataSourceName, Load
FROM LoadForecast
WHERE MarketName = 'AESO'
  AND EffectiveDateTime >= DATEADD(day, -{DAYS}, GETDATE())
ORDER BY EffectiveDateTime ASC;
