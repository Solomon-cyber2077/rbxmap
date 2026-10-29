--@desc Creates leaderstats (Coins) for each player.
local Players = game:GetService("Players")
local Format = require(script.Format)

Players.PlayerAdded:Connect(function(player)
	local stats = Instance.new("Folder")
	stats.Name = "leaderstats"
	stats.Parent = player
	local coins = Instance.new("IntValue")
	coins.Name = "Coins"
	coins.Value = 0
	coins.Parent = stats
	print(Format.coins(coins.Value))
end)
