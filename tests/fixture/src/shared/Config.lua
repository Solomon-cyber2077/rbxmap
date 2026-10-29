local Config = {}

Config.LOBBY_SIZE = 120
Config.PRICES = {
	Sword = 100,
	Shield = 75,
}

function Config.getPrice(itemId: string): number?
	return Config.PRICES[itemId]
end

return Config
